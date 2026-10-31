# -*- coding: utf-8 -*-
"""
Testes de integridade — Diário Alimentar (index.html)

Como rodar:
    pip install pytest playwright
    playwright install chromium
    pytest test_diario_alimentar.py -v

O que faz:
  • Abre o index.html num Chromium real (Playwright)
  • Substitui a biblioteca supabase-js por um simulador em memória
    (nenhuma chamada de rede sai para o Supabase de verdade)
  • Exercita cada fluxo do app e verifica o resultado
  • Permite forçar falhas (upload, login, áudio) para testar a resiliência

Cobertura:
  Inicialização · Login/código de acesso · Sessão · Formulário · Compressão de
  imagem · Envio (foto/comentário/áudio) · Timeline e arco do dia · Painel do
  pesquisador · Busca · CSV · Config · Logout · Modo demonstração · Falhas de
  rede · Proteção contra XSS
"""
import io
import json
import os
import re
import struct
import zlib
import pytest
from playwright.sync_api import sync_playwright

HTML = os.environ.get("APP_HTML", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "index.html"))
ACCESS_CODE = "DIARIO2026"
RESEARCHER_EMAIL = "erika@pesquisa.br"
RESEARCHER_PW = "Senha-Forte-123"
REG_CODE = "Pesquisa@2026"

# ---------------------------------------------------------------------------
# Simulador do supabase-js — grava em window.__db e respeita window.__fail
# ---------------------------------------------------------------------------
SUPA_SHIM = r"""
window.__db = { rows: [], uploads: [], session: null, profiles: [], users: [], regCode: "Pesquisa@2026", researchers: ["res-1"] };
// Persistência entre reloads (como o supabase-js faz): banco e sessão em sessionStorage
try{const saved=JSON.parse(sessionStorage.getItem("__shimdb")||"null");if(saved)Object.assign(window.__db,saved)}catch(e){}
setInterval(()=>{try{sessionStorage.setItem("__shimdb",JSON.stringify(window.__db))}catch(e){}},100);
window.addEventListener("beforeunload",()=>{try{sessionStorage.setItem("__shimdb",JSON.stringify(window.__db))}catch(e){}});
window.__fail = window.__fail || {};   // {signIn:true, upload:'.jpg'|'.m4a'|'all', insert:true, select:true}
const RESEARCHER = {email:"erika@pesquisa.br", password:"Senha-Forte-123", uid:"res-1"};
function _isRes(){const s=window.__db.session;return !!(s&&window.__db.researchers.includes(s.user.id))}
function _uid(){return window.__db.session?window.__db.session.user.id:null}
function _tbl(t){if(t==="participantes")return window.__db.profiles;
  if(t==="pesquisadores")return _isRes()?window.__db.researchers.map(id=>({user_id:id,auth_uid:_uid()})):[];
  return window.__db.rows}
function _q(t){
  const self={ _f:[], _order:null, _limit:null,
    select(){return self}, order(c,o){self._order=[c,o];return self}, limit(n){self._limit=n;return self},
    eq(c,v){self._f.push(r=>r[c]===v);return self},
    gte(c,v){self._f.push(r=>r[c]>=v);return self},
    lt(c,v){self._f.push(r=>r[c]<v);return self},
    then(res){
      if(window.__fail.select) return res({data:null,error:{message:"select failed"}});
      let d=_tbl(t).filter(r=>_isRes()||(_uid()&&r.auth_uid===_uid()));   // RLS
      d=d.filter(r=>self._f.every(f=>f(r)));
      if(self._order){const[c,o]=self._order;d=[...d].sort((a,b)=>(a[c]<b[c]?-1:a[c]>b[c]?1:0)*(o&&o.ascending===false?-1:1))}
      if(self._limit)d=d.slice(0,self._limit);
      res({data:d,error:null});
    },
    async upsert(row,opts){
      if(!_uid()) return {error:{message:"RLS: not authenticated"}};
      const key=(opts&&opts.onConflict)||"participante_id"; const r=Object.assign({},row,{auth_uid:_uid()});
      const arr=_tbl(t); const i=arr.findIndex(x=>x[key]===r[key]);
      if(i>=0){ if(arr[i].auth_uid!==_uid()) return {error:{message:"RLS: update denied"}}; arr[i]=Object.assign({},arr[i],r) } else arr.push(r);
      return {error:null};
    },
    async insert(row){
      if(window.__fail.insert) return {error:{message:"insert failed"}};
      if(!_uid()) return {error:{message:"RLS: not authenticated"}};
      const r=Object.assign({},Array.isArray(row)?row[0]:row); r.auth_uid=_uid();
      _tbl(t).push(r); return {error:null};
    }};
  return self;
}
window.supabase={createClient:(url,key)=>{
  window.__db.clientArgs={url,key};
  return {
    auth:{
      async getSession(){return {data:{session:window.__db.session}}},
      async signInAnonymously(){
        if(window.__fail.signIn) return {data:{session:null},error:{message:"auth disabled"}};
        const id="anon-"+Math.random().toString(36).slice(2,8);
        window.__db.session={user:{id,is_anonymous:true},access_token:"at-"+id,refresh_token:"rt-"+id};
        (window.__db.tokens=window.__db.tokens||{})["rt-"+id]=window.__db.session;
        return {data:{session:window.__db.session},error:null};
      },
      async setSession({access_token,refresh_token}){
        const sess=(window.__db.tokens||{})[refresh_token];
        if(!sess||window.__fail.restore) return {data:{session:null},error:{message:"Invalid Refresh Token"}};
        window.__db.session=sess; return {data:{session:sess},error:null};
      },
      async signInWithPassword({email,password}){
        if(email===RESEARCHER.email&&password===RESEARCHER.password){
          window.__db.session={user:{id:RESEARCHER.uid,email,is_anonymous:false}};
          return {data:{session:window.__db.session,user:window.__db.session.user},error:null}}
        const u=window.__db.users.find(u=>u.email===email&&u.password===password);
        if(u){window.__db.session={user:{id:u.id,email,is_anonymous:false}};return {data:{session:window.__db.session,user:window.__db.session.user},error:null}}
        return {data:{session:null,user:null},error:{message:"Invalid login credentials"}};
      },
      async signUp({email,password,options}){
        const meta=(options&&options.data)||{};
        if(window.__db.signupsDisabled) return {data:{user:null,session:null},error:{message:"Signups not allowed for this instance",status:422}};
        if(window.__db.users.some(u=>u.email===email)||email===RESEARCHER.email){
          // GoTrue com confirmação de e-mail: anti-enumeração (usuário "fantasma" sem identidades); sem confirmação: erro explícito
          if(window.__db.confirmEmail) return {data:{user:{id:"ghost",email,identities:[]},session:null},error:null};
          return {data:{user:null,session:null},error:{message:"User already registered",status:422}}}
        // gatilho tg_novo_usuario: código errado → GoTrue devolve SÓ a mensagem genérica
        if(meta.codigo_cadastro!==undefined && meta.codigo_cadastro!==window.__db.regCode) return {data:{user:null,session:null},error:{message:"Database error saving new user",status:500}};
        const id="user-"+Math.random().toString(36).slice(2,8); window.__db.users.push({id,email,password,meta});
        if(meta.codigo_cadastro!==undefined) window.__db.researchers.push(id);
        if(window.__db.confirmEmail) return {data:{user:{id,email},session:null},error:null};
        window.__db.session={user:{id,email,is_anonymous:false}}; return {data:{user:{id,email},session:window.__db.session},error:null};
      },
      async resetPasswordForEmail(email,opts){
        if(window.__fail.reset) return {data:null,error:{message:"rate limit exceeded",status:429}};
        (window.__db.resetMails=window.__db.resetMails||[]).push({email,redirectTo:opts&&opts.redirectTo}); return {data:{},error:null}},
      async updateUser({password}){
        if(!window.__db.session) return {data:{user:null},error:{message:"Auth session missing!"}};
        if(password==="MesmaSenha123") return {data:{user:null},error:{message:"New password should be different from the old password."}};
        window.__db.lastPassword=password; return {data:{user:window.__db.session.user},error:null}},
      async signOut(){window.__db.session=null;(window.__authCbs||[]).forEach(cb=>cb("SIGNED_OUT",null));return {error:null}},
      onAuthStateChange(cb){(window.__authCbs=window.__authCbs||[]).push(cb);return {data:{subscription:{unsubscribe(){}}}}}
    },
    async rpc(fn,args){
      if(window.__db.sqlNotApplied) return {data:null,error:{code:"PGRST202",message:"Could not find the function public."+fn+" in the schema cache"}};
      if(fn==="validar_codigo_cadastro") return {data:args.codigo===window.__db.regCode,error:null};
      if(fn==="definir_codigo_cadastro"){ if(!_isRes()) return {error:{message:"nao autorizado"}}; if((args.novo||"").length<6) return {error:{message:"codigo muito curto"}}; window.__db.regCode=args.novo; return {data:null,error:null} }
      return {error:{message:"unknown rpc"}};
    },
    from:(t)=>_q(t),
    storage:{from:(bucket)=>({
      async upload(path,buf,opts){
        const f=window.__fail.upload;
        if(f==='all'||(f&&path.endsWith(f))) return {data:null,error:{message:"upload failed "+path}};
        if(!_uid()) return {data:null,error:{message:"RLS: not authenticated"}};
        window.__db.uploads.push({bucket,path,size:buf.byteLength,contentType:opts&&opts.contentType});
        return {data:{path},error:null};
      },
      getPublicUrl:(p)=>({data:{publicUrl:p?("https://test.supabase.co/storage/v1/object/public/"+bucket+"/"+p):""}})
    })}
  };
}};
"""

def _png(w, h):
    """PNG RGB com ruído aleatório (incompressível), válido, de w×h — para testar compressão."""
    raw = b"".join(b"\x00" + os.urandom(3 * w) for _ in range(h))
    def chunk(t, d): 
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))

SMALL_JPG = {"name": "foto.jpg", "mimeType": "image/jpeg", "buffer": b"\xff\xd8\xff\xe0" + b"\x00" * 3000}

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"])
        yield b
        b.close()

@pytest.fixture
def page(browser):
    """Página nova, localStorage limpo, supabase simulado, coletor de erros JS."""
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, permissions=["microphone"])
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.route("**/supabase-js@2*", lambda r: r.fulfill(status=200, content_type="application/javascript", body=SUPA_SHIM))
    pg.route("**/fonts.g*", lambda r: r.fulfill(status=200, content_type="text/css", body=""))
    pg.goto("file://" + HTML)
    pg.wait_for_function("typeof supa !== 'undefined'")
    pg.evaluate("localStorage.clear(); sessionStorage.clear()")
    pg.reload()
    pg.wait_for_function("typeof supa !== 'undefined'")
    yield pg
    ctx.close()

@pytest.fixture
def logged(page):
    """Participante 'Ana' já logado."""
    login(page, "Ana")
    return page

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def login(pg, name, code=ACCESS_CODE, profile=True):
    """Login do participante. Por padrão preenche o perfil no primeiro acesso (tela obrigatória)."""
    pg.fill("#nameInput", name); pg.fill("#codeInput", code); pg.click("#enterBtn")
    pg.wait_for_timeout(500)
    if profile and visible(pg, "#screen-profile"):
        fill_profile(pg, nome=name + " Jr", idade="7", resp="Mãe")

def fill_profile(pg, nome="Leo", idade="7", resp="Mãe", diag="", restr="", contato=""):
    pg.fill("#pfNome", nome); pg.fill("#pfIdade", str(idade)); pg.select_option("#pfResp", label=resp) if resp else None
    if diag: pg.select_option("#pfDiag", label=diag)
    pg.fill("#pfRestr", restr); pg.fill("#pfContato", contato)
    pg.click("#pfSaveBtn"); pg.wait_for_timeout(500)

def toast(pg):
    return pg.inner_text("#toast")

def visible(pg, sel):
    return not pg.evaluate(f"document.querySelector('{sel}').classList.contains('hidden')")

def set_photo(pg, file=SMALL_JPG):
    pg.set_input_files("#fileGallery", file); pg.wait_for_timeout(400)

def record_audio(pg, ms=1200):
    pg.click("#recordBtn"); pg.wait_for_timeout(ms); pg.click("#stopBtn")
    pg.wait_for_function("document.getElementById('recordStatus').textContent.includes('pronto')", timeout=4000)

def send(pg):
    pg.click("#sendBtn"); pg.wait_for_timeout(900)

def db(pg):
    return pg.evaluate("window.__db")

def admin_login(pg, email=RESEARCHER_EMAIL, pw=RESEARCHER_PW):
    pg.click('button[data-tab="pesquisador"]'); pg.wait_for_timeout(200)
    pg.fill("#adminEmail", email); pg.fill("#adminPw", pw); pg.click("#adminLoginBtn"); pg.wait_for_timeout(600)

def fill_meal_photo(pg, meal="Almoço"):
    pg.click(f'.meal[data-meal="{meal}"]'); set_photo(pg)

# ===========================================================================
# 1. Inicialização
# ===========================================================================
class TestInicializacao:
    def test_carrega_sem_erros_js(self, page):
        assert page.errors == []

    def test_supabase_conecta_com_chaves_embutidas_sem_salvar(self, page):
        assert page.evaluate("!!supa")
        args = db(page)["clientArgs"]
        assert args["url"].startswith("https://") and "supabase.co" in args["url"]
        assert args["key"].startswith("sb_publishable_")

    def test_aviso_de_configuracao_oculto(self, page):
        assert not visible(page, "#cfgWarn")

    def test_url_nao_termina_com_rest_v1(self, page):
        assert not db(page)["clientArgs"]["url"].endswith("/rest/v1/")
        assert not db(page)["clientArgs"]["url"].endswith("/")

    def test_tela_de_login_visivel_e_app_oculto(self, page):
        assert visible(page, "#screen-login") and not visible(page, "#screen-app")

    def test_abas_alternam(self, page):
        page.click('button[data-tab="pesquisador"]')
        assert visible(page, "#tab-pesquisador") and not visible(page, "#tab-participante")
        page.click('button[data-tab="participante"]')
        assert visible(page, "#tab-participante")

    def test_nao_existe_aba_config_publica(self, page):
        assert page.evaluate("document.querySelectorAll('.nav button').length") == 2
        assert page.evaluate("document.querySelector('[data-tab=\"config\"]')") is None

# ===========================================================================
# 2. Login / código de acesso
# ===========================================================================
class TestLogin:
    def test_nome_curto_rejeitado(self, page):
        login(page, "A"); assert "nome" in toast(page).lower(); assert visible(page, "#screen-login")

    def test_codigo_vazio_rejeitado(self, page):
        login(page, "Ana", ""); assert "código" in toast(page).lower()

    def test_codigo_errado_rejeitado(self, page):
        login(page, "Ana", "ERRADO"); assert "incorreto" in toast(page).lower(); assert visible(page, "#screen-login")

    def test_codigo_aceito_case_insensitive(self, page):
        login(page, "Ana", "diario2026", profile=False); assert visible(page, "#screen-profile")
        assert not visible(page, "#screen-login")

    def test_login_cria_sessao_anonima(self, logged):
        assert db(logged)["session"] is not None

    def test_saudacao_com_nome(self, logged):
        g = logged.inner_text("#greet")
        assert "Ana" in g and re.match(r"^(Bom dia|Boa tarde|Boa noite), Ana$", g)

    def test_sessao_persiste_apos_reload(self, logged):
        pid = logged.evaluate("participant.id")
        logged.reload(); logged.wait_for_function("typeof supa !== 'undefined'"); logged.wait_for_timeout(300)
        assert visible(logged, "#screen-app") and logged.evaluate("participant.id") == pid

    def test_mesmo_nome_mantem_id_nome_diferente_gera_novo(self, logged):
        pid = logged.evaluate("participant.id")
        logged.click("#logoutLink"); login(logged, "ana")
        assert logged.evaluate("participant.id") == pid
        logged.click("#logoutLink"); login(logged, "Bruno")
        assert logged.evaluate("participant.id") != pid

    def test_falha_no_signin_bloqueia_com_aviso(self, page):
        page.evaluate("window.__fail.signIn=true")
        login(page, "Ana", profile=False)
        assert visible(page, "#screen-login") and "conectar" in toast(page).lower()

# ===========================================================================
# 2b. Perfil do participante
# ===========================================================================
class TestPerfilParticipante:
    def test_primeiro_acesso_exige_perfil(self, page):
        login(page, "Ana", profile=False)
        assert visible(page, "#screen-profile") and not visible(page, "#screen-app")
        assert page.input_value("#pfNome") == "Ana", "nome pré-preenchido com o nome informado"

    def test_validacoes(self, page):
        login(page, "Ana", profile=False)
        page.fill("#pfNome", "L"); page.click("#pfSaveBtn"); assert "nome" in toast(page).lower()
        page.fill("#pfNome", "Leo"); page.fill("#pfIdade", ""); page.click("#pfSaveBtn"); assert "idade" in toast(page).lower()
        page.fill("#pfIdade", "200"); page.click("#pfSaveBtn"); assert "idade" in toast(page).lower()
        page.fill("#pfIdade", "7"); page.click("#pfSaveBtn"); assert "preenchendo" in toast(page).lower()
        assert visible(page, "#screen-profile") and db(page)["profiles"] == []

    def test_salva_perfil_no_banco_e_abre_app(self, page):
        login(page, "Ana", profile=False)
        fill_profile(page, nome="Leo", idade="7", resp="Mãe", diag="TEA — nível 2 de suporte", restr="Só crocantes", contato="11 99999-0000")
        assert visible(page, "#screen-app")
        prof = db(page)["profiles"]; assert len(prof) == 1; p = prof[0]
        assert p["participante_id"] == page.evaluate("participant.id") and p["nome_responsavel"] == "Ana"
        assert p["nome_pessoa"] == "Leo" and p["idade"] == 7 and p["responsavel"] == "Mãe"
        assert p["diagnostico"] == "TEA — nível 2 de suporte" and p["restricoes"] == "Só crocantes" and p["contato"] == "11 99999-0000"
        assert p["auth_uid"] == db(page)["session"]["user"]["id"]

    def test_campos_opcionais_vazios_viram_null(self, page):
        login(page, "Ana", profile=False); fill_profile(page)
        p = db(page)["profiles"][0]
        assert p["diagnostico"] is None and p["restricoes"] is None and p["contato"] is None

    def test_perfil_nao_e_pedido_de_novo_apos_reload(self, page):
        login(page, "Ana"); page.reload(); page.wait_for_function("typeof supa !== 'undefined'"); page.wait_for_timeout(500)
        assert visible(page, "#screen-app") and not visible(page, "#screen-profile")

    def test_sair_e_entrar_de_novo_restaura_sessao_perfil_e_historico(self, page):
        """Regressão: antes, sair/entrar criava novo uid anônimo → perfil sumia, upsert falhava, histórico sumia."""
        login(page, "Ana"); fill_meal_photo(page); send(page)
        uid1 = db(page)["session"]["user"]["id"]
        page.click("#logoutLink"); page.wait_for_timeout(300)
        assert db(page)["session"] is None
        login(page, "Ana", profile=False); page.wait_for_timeout(500)
        assert db(page)["session"]["user"]["id"] == uid1, "mesma sessão anônima restaurada"
        assert visible(page, "#screen-app"), "perfil existente → não pede de novo"
        assert page.inner_text("#todayCount") == "1 registro"
        page.click("#editProfileLink"); page.fill("#pfIdade", "9"); page.click("#pfSaveBtn"); page.wait_for_timeout(400)
        assert db(page)["profiles"][0]["idade"] == 9 and len(db(page)["profiles"]) == 1, "upsert funciona na mesma sessão"

    def test_sessao_antiga_invalida_gera_participante_novo_sem_erro(self, page):
        login(page, "Ana"); pid1 = page.evaluate("participant.id")
        page.click("#logoutLink"); page.wait_for_timeout(300)
        page.evaluate("window.__fail.restore=true")   # refresh token expirou/revogado
        login(page, "Ana", profile=False); page.wait_for_timeout(400)
        assert page.evaluate("participant.id") != pid1, "não reaproveita ID cujos dados não consegue acessar"
        assert visible(page, "#screen-profile"), "pede perfil para o novo ID"
        fill_profile(page); assert visible(page, "#screen-app")

    def test_perfil_recuperado_do_banco_se_local_apagado(self, page):
        login(page, "Ana")
        page.evaluate("localStorage.removeItem(profileKey())")
        page.reload(); page.wait_for_function("typeof supa !== 'undefined'"); page.wait_for_timeout(600)
        assert visible(page, "#screen-app"), "perfil veio do servidor"

    def test_pesquisador_logado_que_entra_como_participante_vira_anonimo(self, page):
        admin_login(page); assert visible(page, "#admin-dash")
        page.click('button[data-tab="participante"]'); login(page, "Ana")
        sess = db(page)["session"]; assert sess["user"]["is_anonymous"] is True
        fill_meal_photo(page); send(page)
        assert db(page)["rows"][0]["auth_uid"] == sess["user"]["id"], "registro não fica com uid do pesquisador"

    def test_logout_do_pesquisador_nao_derruba_participante(self, page):
        login(page, "Ana"); uid1 = db(page)["session"]["user"]["id"]
        admin_login(page); page.click("#adminLogoutLink"); page.wait_for_timeout(400)
        assert db(page)["session"] and db(page)["session"]["user"]["id"] == uid1
        page.click('button[data-tab="participante"]'); assert visible(page, "#screen-app")

    def test_editar_perfil_faz_update_nao_duplica(self, page):
        login(page, "Ana"); page.click("#editProfileLink"); page.wait_for_timeout(200)
        assert visible(page, "#screen-profile") and page.input_value("#pfIdade") == "7"
        page.fill("#pfIdade", "8"); page.click("#pfSaveBtn"); page.wait_for_timeout(400)
        prof = db(page)["profiles"]; assert len(prof) == 1 and prof[0]["idade"] == 8 and visible(page, "#screen-app")

    def test_voltar_sem_alterar_mantem_dados(self, page):
        login(page, "Ana"); page.click("#editProfileLink"); page.wait_for_timeout(200)
        page.fill("#pfIdade", "99"); page.click("#pfBackBtn"); page.wait_for_timeout(200)
        assert visible(page, "#screen-app") and db(page)["profiles"][0]["idade"] == 7

    def test_voltar_no_primeiro_acesso_retorna_ao_login(self, page):
        login(page, "Ana", profile=False); page.click("#pfBackBtn"); page.wait_for_timeout(300)
        assert visible(page, "#screen-login")

    def test_falha_no_servidor_mantem_tela_de_perfil(self, page):
        login(page, "Ana", profile=False)
        page.evaluate("window.__db.session=null; window.__fail.signIn=true")
        fill_profile(page)
        assert visible(page, "#screen-profile") and "não foi possível" in toast(page).lower()

    def test_participante_nao_le_perfil_de_outro(self, page):
        login(page, "Ana"); page.click("#logoutLink"); page.wait_for_timeout(300); login(page, "Bruno")
        names = page.evaluate("""(async()=>{const {data}=await supa.from('participantes').select('*');return data.map(p=>p.nome_responsavel)})()""")
        assert names == ["Bruno"]

# ===========================================================================
# 2c. Cadastro de pesquisador
# ===========================================================================
class TestCadastroPesquisador:
    def open(self, pg):
        pg.click('button[data-tab="pesquisador"]'); pg.wait_for_timeout(200); pg.click("#showSignupBtn"); pg.wait_for_timeout(150)

    def signup(self, pg, email="nova@pesquisa.br", nome="Nova", pw="SenhaForte1", pw2=None, code=REG_CODE):
        pg.fill("#suEmail", email); pg.fill("#suNome", nome); pg.fill("#suPw", pw); pg.fill("#suPw2", pw if pw2 is None else pw2); pg.fill("#suCode", code)
        pg.click("#signupBtn"); pg.wait_for_timeout(600)

    def test_alterna_entre_login_e_cadastro(self, page):
        self.open(page)
        assert visible(page, "#admin-signup-card") and not visible(page, "#admin-login-card")
        page.click("#showLoginBtn"); assert visible(page, "#admin-login-card")

    def test_validacoes_locais(self, page):
        self.open(page)
        self.signup(page, email="x"); assert "e-mail" in toast(page).lower()
        self.signup(page, nome="N"); assert "nome" in toast(page).lower()
        self.signup(page, pw="curta"); assert "8 caracteres" in toast(page).lower()
        self.signup(page, pw2="Diferente1"); assert "coincidem" in toast(page).lower()
        self.signup(page, code=""); assert "código" in toast(page).lower()
        assert db(page)["users"] == []

    def test_codigo_errado_recusado_pelo_servidor(self, page):
        self.open(page); self.signup(page, code="errado")
        assert "inválido" in toast(page).lower() and db(page)["users"] == [] and not visible(page, "#admin-dash")

    def test_codigo_errado_nao_chega_a_criar_usuario(self, page):
        """A pré-validação evita o erro genérico do GoTrue ('Database error saving new user')."""
        self.open(page); page.evaluate("()=>{window.__db.signUpCalls=0; const o=supa.auth.signUp; supa.auth.signUp=async(a)=>{window.__db.signUpCalls++;return o(a)}}")
        self.signup(page, code="errado")
        assert page.evaluate("window.__db.signUpCalls") == 0

    def test_erro_generico_do_gotrue_e_traduzido(self, page):
        """Se o gatilho recusar (ex.: código trocado entre a validação e o signUp), a mensagem é compreensível."""
        self.open(page)
        page.evaluate("const o=supa.rpc.bind(supa); supa.rpc=async(f,a)=>f==='validar_codigo_cadastro'?{data:true,error:null}:o(f,a)")
        self.signup(page, code="errado")
        t = toast(page).lower(); assert "recusou" in t and "código" in t and "sql" in t
        assert "database error" not in t

    def test_sql_nao_aplicado_mensagem_clara(self, page):
        page.evaluate("window.__db.sqlNotApplied=true"); self.open(page); self.signup(page)
        assert "sql de instalação" in toast(page).lower() and db(page)["users"] == []

    def test_cadastro_desativado_no_supabase(self, page):
        page.evaluate("window.__db.signupsDisabled=true"); self.open(page); self.signup(page)
        assert "allow new users" in toast(page).lower()

    def test_email_existente_com_confirmacao_ativa_anti_enumeracao(self, page):
        page.evaluate("window.__db.confirmEmail=true"); self.open(page); self.signup(page, email=RESEARCHER_EMAIL)
        assert "já tem conta" in toast(page).lower() and not visible(page, "#admin-dash")

    def test_diagnostico_ambiente_ok(self, page):
        self.open(page); page.fill("#suCode", REG_CODE); page.click("#diagBtn"); page.wait_for_timeout(700)
        d = page.inner_text("#diagOut")
        for k in ("Conectado", "SQL de instalação aplicado", "é válido", "Login anônimo funciona", "participantes acessível", "registros acessível"):
            assert k in d, k
        assert "❌" not in d
        assert page.evaluate("window.__db.session") is None, "diagnóstico não deixa sessão anônima aberta"

    def test_diagnostico_detecta_sql_ausente_e_codigo_invalido(self, page):
        page.evaluate("window.__db.sqlNotApplied=true"); self.open(page); page.fill("#suCode", "errado"); page.click("#diagBtn"); page.wait_for_timeout(700)
        d = page.inner_text("#diagOut"); assert "SQL de instalação não executado" in d and "setup.sql" in d
        page.evaluate("window.__db.sqlNotApplied=false"); page.click("#diagBtn"); page.wait_for_timeout(700)
        assert "NÃO é válido" in page.inner_text("#diagOut")

    def test_diagnostico_detecta_login_anonimo_desativado(self, page):
        page.evaluate("window.__fail.signIn=true"); self.open(page); page.click("#diagBtn"); page.wait_for_timeout(700)
        assert "Login anônimo desativado" in page.inner_text("#diagOut")

    def test_codigo_correto_cria_conta_e_entra(self, page):
        self.open(page); self.signup(page)
        d = db(page)
        assert len(d["users"]) == 1 and d["users"][0]["email"] == "nova@pesquisa.br" and d["users"][0]["meta"]["nome"] == "Nova"
        assert d["users"][0]["id"] in d["researchers"], "gatilho registrou como pesquisador"
        assert visible(page, "#admin-dash")
        assert page.input_value("#suCode") == "" and page.input_value("#suPw") == ""

    def test_codigo_nunca_vai_para_o_html_nem_e_checado_no_cliente(self, page):
        html = open(HTML, encoding="utf-8").read()
        assert REG_CODE not in html
        assert "codigo_cadastro" in html, "código é enviado como metadado para o servidor validar"

    def test_email_ja_cadastrado(self, page):
        self.open(page); self.signup(page, email=RESEARCHER_EMAIL)
        assert "já tem conta" in toast(page).lower()

    def test_com_confirmacao_de_email_orienta_e_volta_ao_login(self, page):
        page.evaluate("window.__db.confirmEmail=true"); self.open(page); self.signup(page)
        assert "confirme pelo link" in toast(page).lower() and visible(page, "#admin-login-card")
        assert page.input_value("#adminEmail") == "nova@pesquisa.br"

    def test_conta_criada_consegue_logar_depois(self, page):
        self.open(page); self.signup(page); page.click("#adminLogoutLink"); page.wait_for_timeout(300)
        admin_login(page, "nova@pesquisa.br", "SenhaForte1"); assert visible(page, "#admin-dash")

    def test_sessao_anonima_nao_e_promovida(self, page):
        login(page, "Ana"); self.open(page); self.signup(page)
        assert db(page)["session"]["user"]["email"] == "nova@pesquisa.br"

    def test_pesquisador_troca_codigo_de_cadastro(self, page):
        admin_login(page); page.click("#adminCfgBtn"); page.wait_for_timeout(200)
        page.fill("#cfgRegCode", "abc"); page.click("#cfgRegCodeBtn"); assert "6 caracteres" in toast(page).lower()
        page.fill("#cfgRegCode", "NovoCodigo2027"); page.click("#cfgRegCodeBtn"); page.wait_for_timeout(300)
        assert db(page)["regCode"] == "NovoCodigo2027" and page.input_value("#cfgRegCode") == ""
        page.click("#adminCfgBackBtn"); page.click("#adminLogoutLink"); page.wait_for_timeout(200); self.open(page)
        self.signup(page, code=REG_CODE); assert "inválido" in toast(page).lower()
        self.signup(page, code="NovoCodigo2027"); assert visible(page, "#admin-dash")


# ===========================================================================
# 2d. Esqueci a senha / esqueci o código
# ===========================================================================
class TestEsqueciSenha:
    def test_participante_ajuda_abre_e_fecha(self, page):
        assert not visible(page, "#forgotCodeBox")
        page.click("#forgotCodeBtn"); assert visible(page, "#forgotCodeBox")
        t = page.inner_text("#forgotCodeBox"); assert "código" in t.lower() and "mesmo nome" in t.lower()
        page.click("#forgotCodeBtn"); assert not visible(page, "#forgotCodeBox")

    def test_pesquisador_link_abre_card_com_email_preenchido(self, page):
        page.click('button[data-tab="pesquisador"]'); page.fill("#adminEmail", "x@y.com"); page.click("#showForgotBtn")
        assert visible(page, "#admin-forgot-card") and not visible(page, "#admin-login-card")
        assert page.input_value("#fgEmail") == "x@y.com"
        page.click("#forgotBackBtn"); assert visible(page, "#admin-login-card")

    def test_email_invalido(self, page):
        page.click('button[data-tab="pesquisador"]'); page.click("#showForgotBtn"); page.fill("#fgEmail", "x"); page.click("#forgotBtn")
        assert "e-mail" in toast(page).lower() and page.evaluate("window.__db.resetMails") is None

    def test_envia_link_com_redirect_para_o_app(self, page):
        page.click('button[data-tab="pesquisador"]'); page.click("#showForgotBtn"); page.fill("#fgEmail", RESEARCHER_EMAIL); page.click("#forgotBtn"); page.wait_for_timeout(400)
        mails = page.evaluate("window.__db.resetMails"); assert len(mails) == 1 and mails[0]["email"] == RESEARCHER_EMAIL
        assert mails[0]["redirectTo"].endswith("index.html"), "volta para o próprio app"
        assert "enviado" in toast(page).lower() and visible(page, "#admin-login-card") and page.input_value("#adminEmail") == RESEARCHER_EMAIL

    def test_mensagem_nao_revela_se_email_existe(self, page):
        page.click('button[data-tab="pesquisador"]'); page.click("#showForgotBtn"); page.fill("#fgEmail", "ninguem@x.com"); page.click("#forgotBtn"); page.wait_for_timeout(300)
        assert "se este e-mail tiver conta" in toast(page).lower()

    def test_rate_limit_traduzido(self, page):
        page.evaluate("window.__fail.reset=true"); page.click('button[data-tab="pesquisador"]'); page.click("#showForgotBtn")
        page.fill("#fgEmail", RESEARCHER_EMAIL); page.click("#forgotBtn"); page.wait_for_timeout(300)
        assert "muitas tentativas" in toast(page).lower()

    def _arrive_by_link(self, page):
        # simula sessão de recuperação criada pelo link + evento PASSWORD_RECOVERY
        page.evaluate("""()=>{window.__db.session={user:{id:'res-1',email:'erika@pesquisa.br',is_anonymous:false},access_token:'a',refresh_token:'r'};
                           (window.__authCbs||[]).forEach(cb=>cb('PASSWORD_RECOVERY',window.__db.session))}""")
        page.wait_for_timeout(300)

    def test_evento_recovery_abre_tela_de_nova_senha(self, page):
        self._arrive_by_link(page)
        assert visible(page, "#tab-pesquisador") and visible(page, "#admin-reset-card") and not visible(page, "#admin-login-card")

    def test_url_com_type_recovery_abre_tela_de_nova_senha(self, page):
        page.goto("about:blank"); page.goto("file://" + HTML + "#access_token=x&type=recovery"); page.wait_for_function("typeof supa !== 'undefined'"); page.wait_for_timeout(400)
        assert visible(page, "#admin-reset-card")

    def test_nova_senha_validacoes(self, page):
        self._arrive_by_link(page)
        page.fill("#rsPw", "curta"); page.fill("#rsPw2", "curta"); page.click("#resetBtn"); assert "8 caracteres" in toast(page).lower()
        page.fill("#rsPw", "NovaSenha123"); page.fill("#rsPw2", "Outra"); page.click("#resetBtn"); assert "coincidem" in toast(page).lower()
        page.fill("#rsPw", "MesmaSenha123"); page.fill("#rsPw2", "MesmaSenha123"); page.click("#resetBtn"); page.wait_for_timeout(300)
        assert "diferente da anterior" in toast(page).lower()
        assert page.evaluate("window.__db.lastPassword") is None

    def test_nova_senha_salva_e_entra_no_painel(self, page):
        self._arrive_by_link(page)
        page.fill("#rsPw", "NovaSenha123"); page.fill("#rsPw2", "NovaSenha123"); page.click("#resetBtn"); page.wait_for_timeout(500)
        assert page.evaluate("window.__db.lastPassword") == "NovaSenha123"
        assert visible(page, "#admin-dash") and "atualizada" in toast(page).lower()
        assert page.input_value("#rsPw") == "" and "type=recovery" not in page.url

    def test_trocar_de_aba_nao_sai_da_tela_de_nova_senha(self, page):
        self._arrive_by_link(page)
        page.click('button[data-tab="participante"]'); page.click('button[data-tab="pesquisador"]'); page.wait_for_timeout(300)
        assert visible(page, "#admin-reset-card")

# ===========================================================================
# 3. Formulário
# ===========================================================================
class TestFormulario:
    def test_enviar_desabilitado_no_inicio(self, logged):
        assert logged.is_disabled("#sendBtn")

    def test_so_refeicao_nao_habilita(self, logged):
        logged.click('.meal[data-meal="Jantar"]'); assert logged.is_disabled("#sendBtn")

    def test_so_foto_nao_habilita(self, logged):
        set_photo(logged); assert logged.is_disabled("#sendBtn")

    def test_refeicao_e_foto_habilitam(self, logged):
        fill_meal_photo(logged); assert not logged.is_disabled("#sendBtn")

    def test_selecao_de_refeicao_exclusiva(self, logged):
        logged.click('.meal[data-meal="Jantar"]'); logged.click('.meal[data-meal="Ceia"]')
        assert logged.evaluate("document.querySelectorAll('.meal.sel').length") == 1
        assert logged.evaluate("chosenMeal") == "Ceia"

    def test_preview_aparece_apos_foto(self, logged):
        set_photo(logged)
        assert visible(logged, "#preview") and logged.inner_text("#shootLabel .shoot-text") == "Trocar foto"

    def test_imagem_grande_e_comprimida(self, logged):
        big = _png(2000, 1400)
        assert len(big) > 500_000, "PNG de teste precisa passar o limiar de compressão"
        set_photo(logged, {"name": "grande.png", "mimeType": "image/png", "buffer": big}); logged.wait_for_timeout(800)
        info = logged.evaluate("({type:chosenFile.type,name:chosenFile.name,size:chosenFile.size})")
        assert info["type"] == "image/jpeg" and info["name"].endswith(".jpg")
        assert info["size"] < len(big)
        dims = logged.evaluate("""new Promise(r=>{const i=new Image();i.onload=()=>r([i.width,i.height]);i.src=document.getElementById('preview').src})""")
        assert max(dims) <= 1600

    def test_imagem_pequena_nao_e_alterada(self, logged):
        set_photo(logged)
        assert logged.evaluate("chosenFile.size") == len(SMALL_JPG["buffer"])

    def test_action_sheet_abre_e_fecha(self, logged):
        logged.click("#shootLabel"); assert visible(logged, "#actionSheet")
        logged.click("#asCancel"); assert not visible(logged, "#actionSheet")

# ===========================================================================
# 4. Áudio
# ===========================================================================
class TestAudio:
    def test_gravar_e_parar_gera_audio(self, logged):
        record_audio(logged)
        a = logged.evaluate("({size:chosenAudio.blob.size,type:chosenAudio.type,ext:chosenAudio.ext})")
        assert a["size"] > 0 and a["type"].startswith("audio/") and a["ext"] in ("m4a", "webm", "ogg")

    def test_extensao_coerente_com_mime(self, logged):
        record_audio(logged)
        t, e = logged.evaluate("[chosenAudio.type, chosenAudio.ext]")
        assert (t.startswith("audio/mp4") and e == "m4a") or (t.startswith("audio/webm") and e == "webm") or (t.startswith("audio/ogg") and e == "ogg")

    def test_preview_de_audio_visivel(self, logged):
        record_audio(logged)
        assert visible(logged, "#audioPreviewWrap") and logged.get_attribute("#audioPreview", "src").startswith("blob:")

    def test_cronometro_durante_gravacao(self, logged):
        logged.click("#recordBtn"); logged.wait_for_timeout(1300)
        assert re.match(r"● \d\d:\d\d", logged.inner_text("#recordStatus"))
        logged.click("#stopBtn")

    def test_apagar_descarta_audio(self, logged):
        record_audio(logged); logged.click("#discardAudioBtn")
        assert logged.evaluate("chosenAudio") is None and not visible(logged, "#audioPreviewWrap")

    def test_nao_envia_enquanto_grava(self, logged):
        fill_meal_photo(logged); logged.click("#recordBtn"); logged.wait_for_timeout(300)
        send(logged)
        assert "grava" in toast(logged).lower() and db(logged)["rows"] == []
        logged.click("#stopBtn")

# ===========================================================================
# 5. Envio
# ===========================================================================
class TestEnvio:
    def test_envio_so_foto(self, logged):
        fill_meal_photo(logged, "Café da manhã"); send(logged)
        d = db(logged)
        assert len(d["uploads"]) == 1 and len(d["rows"]) == 1
        up, row = d["uploads"][0], d["rows"][0]
        assert up["bucket"] == "refeicoes" and up["contentType"] == "image/jpeg" and up["size"] == len(SMALL_JPG["buffer"])
        assert row["refeicao"] == "Café da manhã" and row["participante_nome"] == "Ana"
        assert row["foto_path"] == up["path"] and row["audio_path"] is None and row["comentario"] is None
        assert re.match(r"^\d{4}-\d\d-\d\dT", row["criado_em"])

    def test_caminho_organizado_por_participante_e_dia(self, logged):
        fill_meal_photo(logged); send(logged)
        pid = logged.evaluate("participant.id"); path = db(logged)["rows"][0]["foto_path"]
        assert re.match(rf"^{pid}/\d{{4}}-\d\d-\d\d/[a-z0-9]+\.jpg$", path)

    def test_envio_com_comentario(self, logged):
        fill_meal_photo(logged); logged.fill("#commentInput", "  Comeu tudo sozinho  "); send(logged)
        assert db(logged)["rows"][0]["comentario"] == "Comeu tudo sozinho"

    def test_comentario_vazio_vira_null(self, logged):
        fill_meal_photo(logged); logged.fill("#commentInput", "   "); send(logged)
        assert db(logged)["rows"][0]["comentario"] is None

    def test_envio_com_audio(self, logged):
        fill_meal_photo(logged); record_audio(logged); send(logged)
        d = db(logged)
        assert len(d["uploads"]) == 2
        photo, audio = d["uploads"]
        assert audio["contentType"].startswith("audio/") and audio["size"] > 0
        assert os.path.splitext(photo["path"])[0] == os.path.splitext(audio["path"])[0], "foto e áudio compartilham o mesmo nome-base"
        assert d["rows"][0]["audio_path"] == audio["path"]
        assert "áudio" in toast(logged).lower()

    def test_formulario_reseta_apos_envio(self, logged):
        fill_meal_photo(logged); logged.fill("#commentInput", "x"); record_audio(logged); send(logged)
        assert logged.evaluate("chosenMeal===null && chosenFile===null && chosenAudio===null")
        assert logged.input_value("#commentInput") == "" and logged.is_disabled("#sendBtn")
        assert not visible(logged, "#preview") and not visible(logged, "#audioPreviewWrap")

    def test_falha_no_upload_da_foto_nao_grava_registro(self, logged):
        logged.evaluate("window.__fail.upload='.jpg'")
        fill_meal_photo(logged); send(logged)
        assert db(logged)["rows"] == [] and "não foi possível" in toast(logged).lower()
        assert not logged.is_disabled("#sendBtn"), "botão deve voltar para permitir nova tentativa"

    def test_falha_no_insert_nao_deixa_app_travado(self, logged):
        logged.evaluate("window.__fail.insert=true")
        fill_meal_photo(logged); send(logged)
        assert "não foi possível" in toast(logged).lower() and not logged.is_disabled("#sendBtn")

    def test_falha_so_no_audio_salva_foto_mesmo_assim(self, logged):
        fill_meal_photo(logged); record_audio(logged)
        ext = logged.evaluate("chosenAudio.ext"); logged.evaluate(f"window.__fail.upload='.{ext}'")
        send(logged)
        d = db(logged)
        assert len(d["rows"]) == 1 and d["rows"][0]["audio_path"] is None and d["rows"][0]["foto_path"]

# ===========================================================================
# 6. Timeline e arco do dia
# ===========================================================================
class TestTimeline:
    def test_vazia_no_inicio(self, logged):
        assert "primeiro registro" in logged.inner_text("#todayList").lower()
        assert logged.inner_text("#todayCount") == "0 registros"

    def test_registro_aparece_lido_do_banco(self, logged):
        fill_meal_photo(logged); logged.fill("#commentInput", "Recusou a salada"); record_audio(logged); send(logged)
        t = logged.inner_text("#todayList")
        assert "Almoço" in t and "Recusou a salada" in t and logged.inner_text("#todayCount") == "1 registro"
        assert logged.evaluate("document.querySelectorAll('#todayList audio').length") == 1
        src = logged.get_attribute("#todayList img", "src"); assert "test.supabase.co" in src

    def test_arco_marca_refeicao_feita(self, logged):
        fill_meal_photo(logged, "Jantar"); send(logged)
        assert logged.evaluate("""document.querySelector('.arc-dot[data-meal="Jantar"]').classList.contains('done')""")
        assert not logged.evaluate("""document.querySelector('.arc-dot[data-meal="Almoço"]').classList.contains('done')""")
        assert logged.evaluate("parseFloat(document.getElementById('arcFill').style.width)") > 0

    def test_outro_nao_afeta_arco(self, logged):
        fill_meal_photo(logged, "Outro"); send(logged)
        assert logged.evaluate("document.querySelectorAll('.arc-dot.done').length") == 0

    def test_registros_de_outro_participante_nao_aparecem(self, logged):
        fill_meal_photo(logged); send(logged)
        logged.click("#logoutLink"); login(logged, "Bruno"); logged.wait_for_timeout(300)
        assert logged.inner_text("#todayCount") == "0 registros"

    def test_registros_de_ontem_nao_aparecem(self, logged):
        logged.evaluate("""window.__db.rows.push({participante_id:participant.id,participante_nome:'Ana',refeicao:'Ceia',
            foto_path:'a/b/c.jpg',audio_path:null,comentario:null,criado_em:new Date(Date.now()-36*3600e3).toISOString()})""")
        logged.evaluate("loadToday()"); logged.wait_for_timeout(300)
        assert logged.inner_text("#todayCount") == "0 registros"

# ===========================================================================
# 7. Painel do pesquisador
# ===========================================================================
class TestPainel:
    def go(self, pg):
        pg.click('button[data-tab="pesquisador"]')

    def auth(self, pg, email=RESEARCHER_EMAIL, pw=RESEARCHER_PW):
        admin_login(pg, email, pw)

    def seed(self, pg):
        login(pg, "Ana"); fill_meal_photo(pg, "Almoço"); pg.fill("#commentInput", "Comeu tudo"); record_audio(pg); send(pg)
        fill_meal_photo(pg, "Jantar"); send(pg)
        pg.click("#logoutLink"); login(pg, "Bruno"); fill_meal_photo(pg, "Ceia"); send(pg)

    def test_senha_errada(self, page):
        self.auth(page, pw="errada")
        assert "incorretos" in toast(page).lower() and not visible(page, "#admin-dash")

    def test_email_errado(self, page):
        self.auth(page, email="outra@x.com")
        assert not visible(page, "#admin-dash")

    def test_campos_vazios(self, page):
        self.auth(page, email="", pw="")
        assert "e-mail e senha" in toast(page).lower() and not visible(page, "#admin-dash")

    def test_login_correto_abre_painel_e_limpa_senha(self, page):
        self.auth(page)
        assert visible(page, "#admin-dash") and page.input_value("#adminPw") == ""
        assert not page.evaluate("window.__db.session.user.is_anonymous")

    def test_sessao_de_pesquisador_persiste_ao_reabrir_aba(self, page):
        self.auth(page)
        page.click('button[data-tab="participante"]'); page.click('button[data-tab="pesquisador"]'); page.wait_for_timeout(300)
        assert visible(page, "#admin-dash") and not visible(page, "#admin-auth")

    def test_logout_volta_para_login_e_encerra_sessao(self, page):
        self.auth(page); page.click("#adminLogoutLink"); page.wait_for_timeout(300)
        assert visible(page, "#admin-auth") and not visible(page, "#admin-dash")
        assert page.evaluate("window.__db.session") is None

    def test_senha_nao_esta_no_html(self):
        html = open(HTML, encoding="utf-8").read()
        assert "Pesquisa@2026" not in html and "DEFAULT_ADMIN_PW" not in html

    def test_participante_anonimo_nao_ve_registros_dos_outros_no_banco(self, page):
        self.seed(page)  # termina logado como Bruno (anônimo)
        rows = page.evaluate("""(async()=>{const {data}=await supa.from('registros').select('*');return data.map(r=>r.participante_nome)})()""")
        assert rows == ["Bruno"]

    def test_pesquisador_ve_todos_os_registros(self, page):
        self.seed(page); self.auth(page)
        rows = page.evaluate("""(async()=>{const {data}=await supa.from('registros').select('*');return data.map(r=>r.participante_nome)})()""")
        assert sorted(rows) == ["Ana", "Ana", "Bruno"]

    def test_estatisticas(self, page):
        self.seed(page); self.auth(page)
        assert page.inner_text("#sPeople") == "2" and page.inner_text("#sPhotos") == "3" and page.inner_text("#sToday") == "3"

    def test_agrupa_por_participante_com_comentario_e_audio(self, page):
        self.seed(page); self.auth(page)
        assert page.evaluate("document.querySelectorAll('#adminList .person').length") == 2
        t = page.inner_text("#adminList")
        assert "Ana" in t and "Bruno" in t and "Comeu tudo" in t and "2 registro(s)" in t
        assert "Ana Jr, 7 anos" in t and "Mãe" in t, "perfil do participante aparece no cabeçalho"
        assert page.evaluate("document.querySelectorAll('#adminList audio').length") == 1

    def test_participante_com_perfil_sem_registros_aparece(self, page):
        login(page, "Carla"); self.auth(page)
        assert "Carla" in page.inner_text("#adminList") and "Nenhum registro ainda" in page.inner_text("#adminList")
        assert page.inner_text("#sPeople") == "1"

    def test_busca_filtra(self, page):
        self.seed(page); self.auth(page)
        page.fill("#adminSearch", "bru"); page.wait_for_timeout(200)
        assert page.evaluate("document.querySelectorAll('#adminList .person').length") == 1
        assert "Bruno" in page.inner_text("#adminList") and "Ana" not in page.inner_text("#adminList .person-h b")

    def test_busca_sem_resultado(self, page):
        self.seed(page); self.auth(page); page.fill("#adminSearch", "zzz"); page.wait_for_timeout(200)
        assert "nenhum" in page.inner_text("#adminList").lower()

    def test_lightbox_abre_e_fecha(self, page):
        self.seed(page); self.auth(page)
        page.click("#adminList img"); assert page.evaluate("document.getElementById('lb').classList.contains('on')")
        page.click("#lb"); assert not page.evaluate("document.getElementById('lb').classList.contains('on')")

    def test_erro_no_select_mostra_mensagem(self, page):
        page.evaluate("window.__fail.select=true"); self.auth(page)
        assert "erro" in page.inner_text("#adminList").lower()

    def test_sessao_derrubada_pelo_supabase_volta_ao_login(self, page):
        self.auth(page); assert visible(page, "#admin-dash")
        page.evaluate("supa.auth.signOut()"); page.wait_for_timeout(300)   # simula token expirado/revogado
        assert visible(page, "#admin-auth") and not visible(page, "#admin-dash")
        assert "expirou" in toast(page).lower()

    def test_config_acessivel_dentro_do_painel(self, page):
        self.auth(page); page.click("#adminCfgBtn"); page.wait_for_timeout(200)
        assert visible(page, "#admin-config") and not visible(page, "#admin-dash")
        assert page.inner_text("#cfgCodeShow") == ACCESS_CODE
        page.click("#adminCfgBackBtn"); assert visible(page, "#admin-dash")

    def test_csv_tem_todas_as_colunas_e_escapa_aspas(self, page):
        login(page, "Ana"); fill_meal_photo(page); page.fill("#commentInput", 'Disse "não quero", depois comeu'); record_audio(page); send(page)
        self.auth(page)
        # Blob.text() descarta o BOM ao decodificar; lemos os bytes brutos para poder verificá-lo
        page.evaluate("""window.__csv=null; const oc=URL.createObjectURL; URL.createObjectURL=(b)=>{ if(b.type&&b.type.startsWith('text/csv')){ b.arrayBuffer().then(ab=>window.__csv=Array.from(new Uint8Array(ab))) } return oc(b) };
                         HTMLAnchorElement.prototype.click=function(){}""")
        page.click("#csvBtn"); page.wait_for_function("window.__csv!==null")
        raw = bytes(page.evaluate("window.__csv"))
        assert raw.startswith(b"\xef\xbb\xbf"), "BOM UTF-8 para o Excel abrir acentos corretamente"
        csv = raw.decode("utf-8-sig")
        lines = csv.splitlines()
        assert lines[0] == "nome_responsavel,participante_id,nome_pessoa,idade,responsavel,diagnostico,restricoes,contato,refeicao,data_hora,comentario,foto_url,audio_url"
        assert len(lines) == 2 and '"Ana Jr","7","Mãe"' in lines[1]
        assert '"Disse ""não quero"", depois comeu"' in lines[1]
        assert lines[1].count("test.supabase.co") == 2, "foto_url e audio_url preenchidas"
        assert "Almoço" in lines[1], "acentos preservados"

# ===========================================================================
# 8. Configuração
# ===========================================================================
class TestConfig:
    def go(self, pg):
        admin_login(pg); pg.click("#adminCfgBtn"); pg.wait_for_timeout(200)

    def test_config_exige_login_de_pesquisador(self, page):
        page.click('button[data-tab="pesquisador"]'); page.wait_for_timeout(200)
        assert not visible(page, "#admin-config") and visible(page, "#admin-auth")
        assert page.evaluate("document.getElementById('cfgUrl').offsetParent") is None, "campos de config não renderizados"

    def test_campos_preenchidos_com_padroes(self, page):
        self.go(page)
        assert page.input_value("#cfgUrl").startswith("https://") and page.input_value("#cfgKey").startswith("sb_publishable_")

    def test_salvar_sem_url_rejeitado(self, page):
        self.go(page); page.fill("#cfgUrl", ""); page.click("#cfgSaveBtn")
        assert "preencha" in toast(page).lower()

    def test_url_com_rest_v1_e_normalizada(self, page):
        self.go(page); page.fill("#cfgUrl", "https://abc.supabase.co/rest/v1/"); page.click("#cfgSaveBtn"); page.wait_for_timeout(200)
        assert db(page)["clientArgs"]["url"] == "https://abc.supabase.co"

    def test_config_nao_guarda_senhas(self, page):
        self.go(page); page.click("#cfgSaveBtn"); page.wait_for_timeout(200)
        cfg = page.evaluate("JSON.parse(localStorage.getItem('diario_config'))")
        assert set(cfg.keys()) == {"url", "key"}

    def test_sql_embutido_tem_regras_de_seguranca(self, page):
        self.go(page); sql = page.inner_text("#sqlBlock")
        for k in ("audio_path", "comentario", "auth_uid", "is_pesquisador", "pesquisadores", "participantes", "codigo_cadastro", "enable row level security", "storage.buckets"):
            assert k in sql
        assert not re.search(r"for select to anon", sql), "nenhuma policy de leitura para quem não está logado"
        assert "grant execute on function public.validar_codigo_cadastro(text) to anon" in sql, "anon só pode validar o código (sim/não)"

    def test_sem_referencias_a_google_drive(self, page):
        html = open(HTML, encoding="utf-8").read()
        assert not re.search(r"drive|anthropic\.com", html, re.I)

# ===========================================================================
# 9. Logout e modo demonstração
# ===========================================================================
class TestSessaoEModoDemo:
    def test_logout_limpa_tudo(self, logged):
        fill_meal_photo(logged); logged.click("#logoutLink")
        assert visible(logged, "#screen-login") and logged.evaluate("participant") is None
        assert logged.evaluate("localStorage.getItem('diario_participant')") is None
        assert logged.input_value("#nameInput") == ""

    def test_modo_demo_quando_biblioteca_nao_carrega(self, browser):
        ctx = browser.new_context(viewport={"width": 390, "height": 844}); pg = ctx.new_page()
        errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("**/supabase-js@2*", lambda r: r.abort()); pg.route("**/fonts.g*", lambda r: r.abort())
        pg.goto("file://" + HTML); pg.wait_for_timeout(600); pg.evaluate("localStorage.clear()")
        assert pg.evaluate("supa") is None and visible(pg, "#cfgWarn")
        login(pg, "Ana", profile=False); assert visible(pg, "#screen-profile")
        fill_profile(pg); assert visible(pg, "#screen-app"), "perfil salvo localmente sem servidor"
        fill_meal_photo(pg); pg.fill("#commentInput", "demo"); send(pg)
        assert "demonstração" in toast(pg).lower()
        t = pg.inner_text("#todayList"); assert "demonstração" in t and "demo" in t
        ctx.close()

# ===========================================================================
# 10. Segurança básica
# ===========================================================================
class TestSeguranca:
    def test_comentario_com_html_e_escapado_na_timeline(self, logged):
        fill_meal_photo(logged); logged.fill("#commentInput", '<img src=x onerror="window.__xss=1"><b>x</b>'); send(logged)
        logged.wait_for_timeout(300)
        assert logged.evaluate("window.__xss") is None
        assert logged.evaluate("document.querySelectorAll('#todayList .meta img, #todayList .meta b b').length") == 0
        assert "<b>x</b>" in logged.inner_text("#todayList")

    def test_nome_com_html_e_escapado_no_painel(self, page):
        login(page, '<script>window.__xss2=1</script>Zé'); fill_meal_photo(page); send(page)
        admin_login(page)
        assert page.evaluate("window.__xss2") is None and "&lt;script&gt;" in page.inner_html("#adminList")

    def test_perfil_com_html_e_escapado_no_painel(self, page):
        login(page, "Ana", profile=False); fill_profile(page, nome='<img src=x onerror="window.__xss3=1">', restr="<b>x</b>")
        admin_login(page)
        assert page.evaluate("window.__xss3") is None and "&lt;img" in page.inner_html("#adminList")

    def test_secret_key_nao_esta_no_html(self):
        html = open(HTML, encoding="utf-8").read()
        assert "sb_secret_" not in html

    def test_sem_erros_js_apos_fluxo_completo(self, logged):
        fill_meal_photo(logged); record_audio(logged); send(logged)
        admin_login(logged)
        assert logged.errors == []
