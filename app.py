# app.py
import os
import re
import unicodedata
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import sqlite3
import streamlit as st

# ============== CONFIGURAÇÃO DA PÁGINA ==============
st.set_page_config(page_title="Controle de Hospedagem 4.0", layout="wide")

# ============== BANCO DE DADOS ======================
DB_DIR = "db"
DB_PATH = os.path.join(DB_DIR, "hospedagem.db")
os.makedirs(DB_DIR, exist_ok=True)

def conectar():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def inicializar_db():
    conn = conectar()
    c = conn.cursor()
    # Unidades
    c.execute("""
        CREATE TABLE IF NOT EXISTS unidades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT,
            localizacao TEXT,
            capacidade INTEGER,
            status TEXT
        )
    """)
    # Locações
    c.execute("""
        CREATE TABLE IF NOT EXISTS locacoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            unidade_id INTEGER,
            checkin DATE,
            checkout DATE,
            hospede TEXT,
            valor REAL,
            plataforma TEXT,
            status_pagamento TEXT,
            FOREIGN KEY(unidade_id) REFERENCES unidades(id)
        )
    """)
    # Despesas
    c.execute("""
        CREATE TABLE IF NOT EXISTS despesas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            unidade_id INTEGER,
            data DATE,
            tipo TEXT,
            valor REAL,
            descricao TEXT,
            FOREIGN KEY(unidade_id) REFERENCES unidades(id)
        )
    """)
    # Precificação
    c.execute("""
        CREATE TABLE IF NOT EXISTS precos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            unidade_id INTEGER,
            temporada TEXT,
            preco_base REAL,
            FOREIGN KEY(unidade_id) REFERENCES unidades(id)
        )
    """)
    conn.commit()
    conn.close()

inicializar_db()

# ============== FUNÇÕES AUXILIARES ==================
def get_unidades():
    conn = conectar()
    df = pd.read_sql("SELECT * FROM unidades", conn)
    conn.close()
    return df

def get_locacoes():
    conn = conectar()
    df = pd.read_sql("SELECT * FROM locacoes", conn)
    conn.close()
    return df

def get_despesas():
    conn = conectar()
    df = pd.read_sql("SELECT * FROM despesas", conn)
    conn.close()
    return df

def get_precos():
    conn = conectar()
    df = pd.read_sql("SELECT * FROM precos", conn)
    conn.close()
    return df

def _norm(s: str) -> str:
    """Normaliza string para comparações (sem acento, lower, trim)."""
    s = str(s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))

def parse_valor_cell(x) -> float:
    """Converte strings de dinheiro em float. Suporta 'R$ 1.234,56', '1,234.56', '1234,56', '1234.56', '(1.234,56)'. """
    if x is None:
        return 0.0
    s = str(x).strip()
    if s == "" or s.lower() in {"nan", "none"}:
        return 0.0
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    s = re.sub(r"[^\d,.\-]", "", s)
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        v = float(s)
        return -v if neg else v
    except Exception:
        return 0.0

def parse_valor_series(series: pd.Series) -> pd.Series:
    return series.apply(parse_valor_cell)

# ----- Helpers Mobile -----
def card(col, titulo, valor, subtitulo=""):
    with col:
        st.metric(titulo, valor, delta=subtitulo)

def resumo_ocupacao(locacoes_df: pd.DataFrame, inicio: date, fim: date):
    """Retorna (noites_ocupadas, taxa_ocupacao%)."""
    if locacoes_df.empty or inicio > fim:
        return 0, 0.0
    noites_total, noites_ocupadas = 0, 0
    # noites por unidade dentro do período
    unidades_ids = locacoes_df["unidade_id"].unique().tolist()
    for uid in unidades_ids:
        dias_janela = pd.date_range(inicio, fim, freq="D")
        noites_total += len(dias_janela)
        locs = locacoes_df[locacoes_df["unidade_id"] == uid]
        for _, loc in locs.iterrows():
            ci = pd.to_datetime(loc["checkin"]).date()
            co = pd.to_datetime(loc["checkout"]).date()
            if ci < co:
                dr = pd.date_range(max(ci, inicio), min(co, fim) - pd.Timedelta(days=1), freq="D")
                noites_ocupadas += len([d for d in dr if inicio <= d.date() <= fim])
    taxa = (noites_ocupadas / noites_total * 100) if noites_total else 0.0
    return int(noites_ocupadas), round(taxa, 1)

def render_locacao_card(row: pd.Series):
    st.markdown(
        f"""
        <div style="border:1px solid #e5e7eb;border-radius:14px;padding:12px;margin-bottom:10px">
          <div style="font-weight:600;margin-bottom:4px">{row.get('nome','')} • {row.get('plataforma','')}</div>
          <div>🧑 {row.get('hospede','')}</div>
          <div>📅 {pd.to_datetime(row.get('checkin')).date()} → {pd.to_datetime(row.get('checkout')).date()}</div>
          <div>💰 R$ {float(row.get('valor') or 0):,.2f}</div>
          <div style="color:#6b7280">#{int(row.get('id')) if 'id' in row else ''} • {row.get('status_pagamento','')}</div>
        </div>
        """,
        unsafe_allow_html=True
    )

# ============== MENU LATERAL ========================
st.sidebar.title("📌 Menu Principal")

# Toggle mobile
MOBILE = st.sidebar.toggle("📱 Modo Mobile", value=False, help="Ativa interface compacta")

menu_principal = st.sidebar.radio(
    "",
    ["🏠 Dashboard", "📊 Relatórios", "🗂 Gestão de Dados", "⚙️ Configurações"]
)

if menu_principal == "🏠 Dashboard":
    aba = "Dashboard de Ocupação"
elif menu_principal == "📊 Relatórios":
    aba = st.sidebar.radio("📈 Tipo de Relatório", ["Relatório de Despesas", "Análise de Receita e Lucro"])
elif menu_principal == "🗂 Gestão de Dados":
    aba = st.sidebar.radio("📁 Dados Cadastrais", ["Cadastro de Unidades", "Locações", "Despesas", "Precificação"])
else:
    aba = st.sidebar.radio("🔧 Opções do Sistema", ["Parâmetros do Sistema", "Exportar/Importar Dados", "Sobre o Sistema"])

# ============== DASHBOARD ===========================
if aba == "Dashboard de Ocupação":
    st.title("🏠 Dashboard de Ocupação - Visão Geral")

    ano_dash = st.number_input("Ano", min_value=2000, max_value=2100, value=date.today().year)
    unidades_dash = get_unidades()
    locacoes_dash = get_locacoes()
    despesas_dash = get_despesas()

    st.subheader("Filtro de Período")
    if MOBILE:
        data_inicio = st.date_input("De", value=date.today().replace(day=1), key="d_i_m")
        data_fim = st.date_input("Até", value=date.today(), key="d_f_m")
    else:
        col1, col2 = st.columns(2)
        with col1:
            data_inicio = st.date_input("Data inicial", value=date.today().replace(day=1))
        with col2:
            data_fim = st.date_input("Data final", value=date.today())

    dias_periodo = pd.date_range(start=data_inicio, end=data_fim, freq="D")
    dias_str = [d.strftime("%d/%m") for d in dias_periodo]

    unidades_opcoes = unidades_dash["nome"].tolist() if not unidades_dash.empty else []
    unidades_selecionadas = st.multiselect("Unidades", unidades_opcoes, default=unidades_opcoes)

    unidades_dash_filtrado = (
        unidades_dash[unidades_dash["nome"].isin(unidades_selecionadas)]
        if unidades_selecionadas else unidades_dash
    )

    plataformas_opcoes = ["Todas"]
    if not locacoes_dash.empty and "plataforma" in locacoes_dash.columns:
        plataformas_opcoes += sorted([p for p in locacoes_dash["plataforma"].dropna().unique().tolist()])
    plataforma_filtro = st.selectbox("Plataforma", plataformas_opcoes, key="dash_plataforma")

    unidade_filtro = st.selectbox(
        "Unidade",
        ["Todas"] + (unidades_dash["nome"].tolist() if not unidades_dash.empty else []),
        key="dash_unidade_filtro"
    )

    # filtros em locações
    if unidade_filtro != "Todas" and not unidades_dash.empty and not locacoes_dash.empty:
        unidade_id = unidades_dash.loc[unidades_dash["nome"] == unidade_filtro, "id"].values[0]
        locacoes_dash = locacoes_dash[locacoes_dash["unidade_id"] == unidade_id]
    if plataforma_filtro != "Todas" and not locacoes_dash.empty:
        locacoes_dash = locacoes_dash[locacoes_dash["plataforma"] == plataforma_filtro]

    # aplica recorte de período
    if not locacoes_dash.empty:
        locacoes_dash = locacoes_dash[
            (pd.to_datetime(locacoes_dash["checkin"]).dt.date <= data_fim) &
            (pd.to_datetime(locacoes_dash["checkout"]).dt.date >= data_inicio)
        ]

    # ====== Cards Mobile (resumo) ======
    if MOBILE:
        # Receita no período
        receita_periodo = 0.0
        if not locacoes_dash.empty:
            # rateia valor por noite no período
            for _, loc in locacoes_dash.iterrows():
                ci = pd.to_datetime(loc["checkin"]).date()
                co = pd.to_datetime(loc["checkout"]).date()
                val = float(loc.get("valor") or 0.0)
                if ci < co:
                    noites_totais = (co - ci).days
                    if noites_totais > 0:
                        noites_no_periodo = pd.date_range(max(ci, data_inicio), min(co, data_fim) - pd.Timedelta(days=1), freq="D")
                        receita_periodo += (val / noites_totais) * len(noites_no_periodo)

        # Despesa no período
        despesas_periodo = 0.0
        if not despesas_dash.empty:
            d = despesas_dash[
                (pd.to_datetime(despesas_dash["data"]).dt.date >= data_inicio) &
                (pd.to_datetime(despesas_dash["data"]).dt.date <= data_fim)
            ]
            if not unidades_selecionadas:
                despesas_periodo = float(d["valor"].sum()) if not d.empty else 0.0
            else:
                # filtra por unidades selecionadas
                d = d.merge(unidades_dash[["id","nome"]], left_on="unidade_id", right_on="id", how="left")
                d = d[d["nome"].isin(unidades_selecionadas)]
                despesas_periodo = float(d["valor"].sum()) if not d.empty else 0.0

        lucro = receita_periodo - despesas_periodo

        # Noites ocupadas e taxa
        noites_ocup, taxa = resumo_ocupacao(locacoes_dash, data_inicio, data_fim)

        c1, c2 = st.columns(2)
        card(c1, "💰 Receita período", f"R$ {receita_periodo:,.2f}")
        card(c2, "💸 Despesas período", f"R$ {despesas_periodo:,.2f}")
        c3, c4 = st.columns(2)
        card(c3, "📈 Lucro líquido", f"R$ {lucro:,.2f}")
        card(c4, "🏨 Ocupação", f"{taxa:.1f}%", f"{noites_ocup} noites")

        # Próximos movimentos (7 dias)
        st.subheader("📅 Próximos movimentos (7 dias)")
        if locacoes_dash.empty:
            st.info("Sem movimentos no período.")
        else:
            hoje = date.today()
            ate = hoje + timedelta(days=7)
            proximos = []
            for _, loc in locacoes_dash.iterrows():
                ci = pd.to_datetime(loc["checkin"]).date()
                co = pd.to_datetime(loc["checkout"]).date()
                if hoje <= ci <= ate:
                    proximos.append(("🟦 Check-in", ci, loc))
                if hoje <= co <= ate:
                    proximos.append(("◧ Check-out", co, loc))
            if not proximos:
                st.info("Nada planejado para os próximos 7 dias.")
            else:
                for tipo, dia, loc in sorted(proximos, key=lambda x: x[1]):
                    st.write(f"{tipo} • {dia.strftime('%d/%m/%Y')} • {loc.get('hospede','')} • {loc.get('plataforma','')}")

    # ====== Tabela calêndrio (desktop e também útil no mobile para overview) ======
    index_nomes = unidades_dash_filtrado["nome"].tolist() + ["Total R$"]
    valores_num = pd.DataFrame(0.0, index=index_nomes, columns=dias_str)
    tabela_icon = pd.DataFrame("", index=index_nomes, columns=dias_str)

    if not unidades_dash_filtrado.empty and not locacoes_dash.empty:
        for _, unidade in unidades_dash_filtrado.iterrows():
            locs = locacoes_dash[locacoes_dash["unidade_id"] == unidade["id"]]
            for _, loc in locs.iterrows():
                checkin = pd.to_datetime(loc["checkin"]).date()
                checkout = pd.to_datetime(loc["checkout"]).date()
                valor = float(loc.get("valor", 0) or 0)

                if checkin == checkout:
                    dias_locados = []
                else:
                    dr = pd.date_range(checkin, checkout - pd.Timedelta(days=1), freq="D").to_pydatetime()
                    dias_locados = [d.date() for d in dr]

                valor_dia = (valor / len(dias_locados)) if len(dias_locados) > 0 else 0.0

                for d in dias_locados:
                    dia_str = d.strftime("%d/%m")
                    if dia_str in dias_str:
                        tabela_icon.loc[unidade["nome"], dia_str] = "🟧"
                        valores_num.loc[unidade["nome"], dia_str] += valor_dia

                if data_inicio <= checkin <= data_fim:
                    dia_checkin = checkin.strftime("%d/%m")
                    if dia_checkin in dias_str:
                        tabela_icon.loc[unidade["nome"], dia_checkin] = "🟦"
                if data_inicio <= checkout <= data_fim:
                    dia_checkout = checkout.strftime("%d/%m")
                    if dia_checkout in dias_str:
                        tabela_icon.loc[unidade["nome"], dia_checkout] = "◧"

    valores_num.loc["Total R$", dias_str] = valores_num[dias_str].sum(axis=0)
    valores_num["Total R$"] = valores_num[dias_str].sum(axis=1)
    valores_num["Valor Líquido (-13%)"] = valores_num["Total R$"] * 0.87
    valores_num["Total Administradora (20%)"] = valores_num["Total R$"] * 0.20

    tabela_visual = tabela_icon.copy()
    for extra_col in ["Total R$", "Valor Líquido (-13%)", "Total Administradora (20%)"]:
        if extra_col not in tabela_visual.columns:
            tabela_visual[extra_col] = ""

    for r in tabela_icon.index:
        for c in dias_str:
            v = float(valores_num.loc[r, c])
            icone = tabela_icon.loc[r, c]
            tabela_visual.loc[r, c] = f"{icone} {v:,.2f}".strip() if v > 0 else icone

    tabela_visual["Total R$"] = valores_num["Total R$"].map(lambda v: f"{v:,.2f}")
    tabela_visual["Valor Líquido (-13%)"] = valores_num["Valor Líquido (-13%)"].map(lambda v: f"{v:,.2f}")
    tabela_visual["Total Administradora (20%)"] = valores_num["Total Administradora (20%)"].map(lambda v: f"{v:,.2f}")

    tabela_visual = tabela_visual[dias_str + ["Total R$", "Valor Líquido (-13%)", "Total Administradora (20%)"]]

    st.markdown(f"**Ocupação Geral ({data_inicio.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')})**")
    st.dataframe(tabela_visual, use_container_width=True)
    st.markdown("""
**Legenda:**
- 🟧 Ocupado o dia todo (com valor)  
- 🟦 Check-in (após 14h)  
- ◧ Check-out (até 11h — sem valor)
""")

# ============== CADASTRO DE UNIDADES ===============
elif aba == "Cadastro de Unidades":
    st.header("Cadastro e Controle de Unidades")
    with st.form("cad_unidade"):
        nome = st.text_input("Nome da Unidade")
        localizacao = st.text_input("Localização")
        capacidade = st.number_input("Capacidade", min_value=1, max_value=20, value=4)
        status = st.selectbox("Status", ["Disponível", "Ocupado", "Manutenção"])
        enviar = st.form_submit_button("Cadastrar", use_container_width=MOBILE)
        if enviar and nome:
            conn = conectar()
            conn.execute(
                "INSERT INTO unidades (nome, localizacao, capacidade, status) VALUES (?, ?, ?, ?)",
                (nome, localizacao, capacidade, status)
            )
            conn.commit()
            conn.close()
            st.success("Unidade cadastrada!")
    st.subheader("Unidades Cadastradas")
    st.dataframe(get_unidades(), use_container_width=True)

# ============== LOCAÇÕES (MOBILE-FRIENDLY) =========
elif aba == "Locações":
    st.header("Cadastro e Importação de Locações")
    unidades = get_unidades()

    # ------ Cadastro manual ------
    with st.form("cad_locacao"):
        if MOBILE:
            unidade = st.selectbox("Unidade", unidades["nome"] if not unidades.empty else [])
            hospede = st.text_input("Hóspede")
            colm = st.columns(2)
            with colm[0]:
                checkin = st.date_input("Check-in", value=date.today())
                plataforma = st.selectbox("Plataforma", ["Airbnb", "Booking", "Direto"])
            with colm[1]:
                checkout = st.date_input("Check-out", value=date.today())
                status_pagamento = st.selectbox("Pagamento", ["Pendente", "Pago"])
            valor = st.number_input("Valor Total", min_value=0.0, format="%.2f")
        else:
            unidade = st.selectbox("Unidade", unidades["nome"] if not unidades.empty else [])
            checkin = st.date_input("Data Check-in", value=date.today())
            checkout = st.date_input("Data Check-out", value=date.today())
            hospede = st.text_input("Hóspede")
            valor = st.number_input("Valor Total da Reserva", min_value=0.0, format="%.2f")
            plataforma = st.selectbox("Plataforma", ["Airbnb", "Booking", "Direto"])
            status_pagamento = st.selectbox("Status do Pagamento", ["Pendente", "Pago"])

        enviar = st.form_submit_button("Cadastrar Locação", use_container_width=MOBILE)
        if enviar and unidade:
            unidade_id = int(unidades.loc[unidades["nome"] == unidade, "id"].values[0])
            conn = conectar()
            conn.execute(
                "INSERT INTO locacoes (unidade_id, checkin, checkout, hospede, valor, plataforma, status_pagamento) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (unidade_id, str(checkin), str(checkout), hospede, valor, plataforma, status_pagamento)
            )
            conn.commit()
            conn.close()
            st.success("Locação cadastrada!")

    # ------ Importação CSV com ; ------
    st.subheader("Importar Locações (CSV com ;)")

    modo_import = st.radio(
        "Modo de importação", ["Acrescentar (append)", "Sobrescrever (limpar antes)"],
        horizontal=not MOBILE
    )
    csv_file = st.file_uploader("Selecione o CSV", type=["csv"])
    if csv_file is not None:
        try:
            df_csv = pd.read_csv(csv_file, sep=";", encoding="latin-1", dtype=str)
        except UnicodeDecodeError:
            df_csv = pd.read_csv(csv_file, sep=";", encoding="utf-8-sig", dtype=str)

        df_csv.columns = [c.strip().lower() for c in df_csv.columns]
        df_csv = df_csv.applymap(lambda x: x.strip() if isinstance(x, str) else x)

        alias = {
            "unidade": ["unidade", "unit", "nome_unidade", "apto", "apartamento", "imovel", "imóvel"],
            "checkin": ["checkin", "check-in", "data_checkin", "entrada", "inicio", "início"],
            "checkout": ["checkout", "check-out", "data_checkout", "saida", "saída", "fim", "final"],
            "hospede": ["hospede", "hóspede", "cliente", "nome_hospede"],
            "valor": ["valor", "valor_total", "preco", "preço", "amount", "price"],
            "plataforma": ["plataforma", "canal", "origem"],
            "status_pagamento": ["status_pagamento", "pagamento", "status", "payment_status"]
        }
        def pick(col_alts):
            for c in col_alts:
                if c in df_csv.columns:
                    return c
            return None
        selected = {k: pick(v) for k, v in alias.items()}
        rename_map = {v: k for k, v in selected.items() if v is not None}
        df_csv = df_csv.rename(columns=rename_map)

        obrigatorias = ["unidade", "checkin", "checkout"]
        faltando = [c for c in obrigatorias if c not in df_csv.columns]
        st.info(f"Colunas lidas: {list(df_csv.columns)}")

        if faltando:
            st.error(f"Faltam colunas obrigatórias no CSV: {', '.join(faltando)}")
        else:
            for col in ["checkin", "checkout"]:
                df_csv[col] = pd.to_datetime(df_csv[col], dayfirst=True, errors="coerce").dt.date

            if "valor" in df_csv.columns:
                df_csv["valor"] = parse_valor_series(df_csv["valor"])
            else:
                df_csv["valor"] = 0.0

            if "plataforma" not in df_csv.columns:
                df_csv["plataforma"] = "Direto"
            else:
                df_csv["plataforma"] = df_csv["plataforma"].fillna("Direto").astype(str)

            if "status_pagamento" not in df_csv.columns:
                df_csv["status_pagamento"] = "Pendente"
            else:
                df_csv["status_pagamento"] = df_csv["status_pagamento"].fillna("Pendente").astype(str)

            st.dataframe(
                df_csv[["unidade","hospede","checkin","checkout","valor","plataforma","status_pagamento"]].head(20),
                use_container_width=True, height=360
            )

            if st.button("Importar para o sistema", use_container_width=MOBILE):
                unidades_df = get_unidades()
                if unidades_df.empty:
                    st.error("Não há unidades cadastradas. Cadastre antes de importar.")
                else:
                    conn = conectar(); cur = conn.cursor()
                    if modo_import == "Sobrescrever (limpar antes)":
                        cur.execute("DELETE FROM locacoes")
                        conn.commit()

                    mapa_unidade = {_norm(n): int(i) for n, i in zip(unidades_df["nome"], unidades_df["id"])}
                    inseridos, pulados = 0, 0
                    for _, row in df_csv.iterrows():
                        try:
                            uid = mapa_unidade.get(_norm(row.get("unidade")))
                            ci = row.get("checkin"); co = row.get("checkout")
                            if not uid or pd.isna(ci) or pd.isna(co):
                                pulados += 1
                                continue
                            hosp = str(row.get("hospede") or "").strip()
                            val = float(row.get("valor") or 0.0)
                            plat = str(row.get("plataforma") or "Direto").strip()
                            stat = str(row.get("status_pagamento") or "Pendente").strip()
                            cur.execute(
                                "INSERT INTO locacoes (unidade_id, checkin, checkout, hospede, valor, plataforma, status_pagamento) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (uid, str(ci), str(co), hosp, val, plat, stat)
                            )
                            inseridos += 1
                        except Exception:
                            pulados += 1
                            continue
                    conn.commit(); conn.close()
                    msg_pref = " (tabela limpa antes)" if modo_import.startswith("Sobre") else " (adicionados)"
                    st.success(f"Importação concluída{msg_pref}. Inseridos: {inseridos} | Pulados: {pulados}")

    # ------ Listagem / Edição / Exclusão ------
    st.subheader("Locações Registradas")

    if MOBILE:
        unidade_loca_filtro = st.selectbox("Unidade", ["Todas"] + (unidades["nome"].tolist() if not unidades.empty else []))
        mes_loca_filtro = st.selectbox("Mês de check-in", ["Todos"] + [str(m).zfill(2) for m in range(1,13)])
    else:
        unidades_lista = ["Todas"] + (unidades["nome"].tolist() if not unidades.empty else [])
        unidade_loca_filtro = st.selectbox("Filtrar por unidade", unidades_lista, key="locacoes_unidade_filtro")
        mes_loca_filtro = st.selectbox("Filtrar por mês de check-in", ["Todos"] + [str(m).zfill(2) for m in range(1,13)], key="locacoes_mes_filtro")

    locacoes = get_locacoes()
    if not locacoes.empty and not unidades.empty:
        locacoes = locacoes.merge(unidades, left_on="unidade_id", right_on="id", suffixes=("", "_unidade"))
        if unidade_loca_filtro != "Todas":
            locacoes = locacoes[locacoes["nome"] == unidade_loca_filtro]
        if mes_loca_filtro != "Todos":
            locacoes = locacoes[pd.to_datetime(locacoes["checkin"]).dt.month == int(mes_loca_filtro)]

        if MOBILE:
            if locacoes.empty:
                st.info("Sem registros para os filtros.")
            else:
                for _, r in locacoes.sort_values("checkin").iterrows():
                    render_locacao_card(r)
            with st.expander("Excluir locação"):
                if not locacoes.empty:
                    id_excluir = st.selectbox("Selecione o ID", locacoes["id"])
                    if st.button("Excluir", type="primary", use_container_width=True):
                        conn = conectar()
                        conn.execute("DELETE FROM locacoes WHERE id=?", (int(id_excluir),))
                        conn.commit(); conn.close()
                        st.success(f"Locação {id_excluir} excluída! Atualize a página.")
        else:
            edited_df = st.data_editor(
                locacoes[["id","nome","checkin","checkout","hospede","valor","plataforma","status_pagamento"]],
                num_rows="dynamic", use_container_width=True, key="editor_locacoes"
            )
            if st.button("Salvar Alterações nas Locações"):
                conn = conectar()
                for _, row in edited_df.iterrows():
                    conn.execute(
                        "UPDATE locacoes SET checkin=?, checkout=?, hospede=?, valor=?, plataforma=?, status_pagamento=? WHERE id=?",
                        (row["checkin"], row["checkout"], row["hospede"], row["valor"], row["plataforma"], row["status_pagamento"], row["id"])
                    )
                conn.commit(); conn.close()
                st.success("Alterações salvas! Recarregue a página para ver os dados atualizados.")

            st.subheader("Excluir Locação")
            id_excluir = st.selectbox("Selecione o ID da locação para excluir", locacoes["id"])
            if st.button("Excluir Locação"):
                conn = conectar(); conn.execute("DELETE FROM locacoes WHERE id=?", (int(id_excluir),))
                conn.commit(); conn.close()
                st.success(f"Locação {id_excluir} excluída!")
    else:
        st.info("Cadastre unidades e locações para visualizar e editar aqui.")

# ============== DESPESAS ============================
elif aba == "Despesas":
    st.header("Registro de Despesas")
    unidades = get_unidades()

    with st.form("cad_despesa"):
        unidade = st.selectbox("Unidade", unidades["nome"] if not unidades.empty else [])
        data_desp = st.date_input("Data", value=date.today())
        tipo = st.selectbox("Tipo", ["Prestação", "Condominio", "Luz", "Internet", "Gás", "Administradora", "Limpeza", "Manutenção", "Insumos", "Outros"])
        valor = st.number_input("Valor", min_value=0.0, format="%.2f")
        descricao = st.text_input("Descrição")
        enviar = st.form_submit_button("Registrar Despesa", use_container_width=MOBILE)
        if enviar and unidade:
            unidade_id = int(unidades.loc[unidades["nome"] == unidade, "id"].values[0])
            conn = conectar()
            conn.execute(
                "INSERT INTO despesas (unidade_id, data, tipo, valor, descricao) VALUES (?, ?, ?, ?, ?)",
                (unidade_id, str(data_desp), tipo, valor, descricao)
            )
            conn.commit()
            conn.close()
            st.success("Despesa registrada!")

    st.subheader("Despesas Registradas")
    despesas = get_despesas()
    if not despesas.empty and not unidades.empty:
        despesas = despesas.merge(unidades, left_on="unidade_id", right_on="id", suffixes=("", "_unidade"))

        unidades_opcoes = unidades["nome"].tolist()
        unidade_filtro = st.selectbox("Filtrar por unidade", ["Todas"] + unidades_opcoes, key="despesa_unidade_filtro")
        meses_lista = ["Todos"] + [str(m).zfill(2) for m in range(1, 13)]
        mes_filtro = st.selectbox("Filtrar por mês", meses_lista, key="despesa_mes_filtro")

        despesas_filtradas = despesas.copy()
        if unidade_filtro != "Todas":
            despesas_filtradas = despesas_filtradas[despesas_filtradas["nome"] == unidade_filtro]
        if mes_filtro != "Todos":
            despesas_filtradas = despesas_filtradas[pd.to_datetime(despesas_filtradas["data"]).dt.month == int(mes_filtro)]

        if MOBILE:
            total = float(despesas_filtradas["valor"].sum()) if not despesas_filtradas.empty else 0.0
            st.metric("Total filtrado", f"R$ {total:,.2f}")
            st.dataframe(despesas_filtradas[["id","nome","data","tipo","valor","descricao"]], use_container_width=True, height=420)
        else:
            edited_df = st.data_editor(
                despesas_filtradas[["id", "nome", "data", "tipo", "valor", "descricao"]],
                num_rows="dynamic", use_container_width=True, key="editor_despesas"
            )
            if st.button("Salvar Alterações nas Despesas"):
                conn = conectar()
                for _, row in edited_df.iterrows():
                    conn.execute(
                        "UPDATE despesas SET data=?, tipo=?, valor=?, descricao=? WHERE id=?",
                        (row["data"], row["tipo"], float(row["valor"]), row["descricao"], int(row["id"]))
                    )
                conn.commit(); conn.close()
                st.success("Alterações salvas! Recarregue a página para ver os dados atualizados.")

        st.subheader("Excluir Despesa")
        if not despesas_filtradas.empty:
            id_excluir = st.selectbox("Selecione o ID da despesa para excluir", despesas_filtradas["id"], key="excluir_despesa")
            if st.button("Excluir Despesa"):
                conn = conectar()
                conn.execute("DELETE FROM despesas WHERE id=?", (int(id_excluir),))
                conn.commit(); conn.close()
                st.success(f"Despesa {id_excluir} excluída!")

        st.subheader("Copiar Despesa")
        if not despesas_filtradas.empty:
            id_copiar = st.selectbox("Selecione o ID da despesa para copiar", despesas_filtradas["id"], key="copiar_despesa")
            if st.button("Copiar Despesa"):
                despesa_copiar = despesas_filtradas.loc[despesas_filtradas["id"] == id_copiar].iloc[0]
                conn = conectar()
                conn.execute(
                    "INSERT INTO despesas (unidade_id, data, tipo, valor, descricao) VALUES (?, ?, ?, ?, ?)",
                    (int(despesa_copiar["unidade_id"]), despesa_copiar["data"], despesa_copiar["tipo"], float(despesa_copiar["valor"]), despesa_copiar["descricao"])
                )
                conn.commit(); conn.close()
                st.success(f"Despesa {id_copiar} copiada!")
    else:
        st.info("Cadastre unidades e despesas para visualizar e editar aqui.")

# ============== PRECIFICAÇÃO ========================
elif aba == "Precificação":
    st.header("Cadastro de Preços Base por Unidade e Temporada")
    unidades = get_unidades()

    with st.form("cad_preco"):
        unidade = st.selectbox("Unidade", unidades["nome"] if not unidades.empty else [])
        temporada = st.selectbox("Temporada", ["Baixa", "Média", "Alta"])
        preco_base = st.number_input("Preço Base", min_value=0.0, format="%.2f")
        enviar = st.form_submit_button("Cadastrar Preço", use_container_width=MOBILE)
        if enviar and unidade:
            unidade_id = int(unidades.loc[unidades["nome"] == unidade, "id"].values[0])
            conn = conectar()
            conn.execute(
                "INSERT INTO precos (unidade_id, temporada, preco_base) VALUES (?, ?, ?)",
                (unidade_id, temporada, preco_base)
            )
            conn.commit(); conn.close()
            st.success("Preço cadastrado!")

    st.subheader("Preços Base Cadastrados")
    precos = get_precos()
    if not precos.empty and not unidades.empty:
        precos = precos.merge(unidades, left_on="unidade_id", right_on="id", suffixes=("", "_unidade"))
        st.dataframe(precos[["nome", "temporada", "preco_base"]], use_container_width=True)
    else:
        st.info("Cadastre unidades e preços para visualizar aqui.")

    st.subheader("Simulação de Valor de Locação")
    unidade_sim = st.selectbox("Unidade para Simulação", unidades["nome"] if not unidades.empty else [], key="simul")
    temporada_sim = st.selectbox("Temporada para Simulação", ["Baixa", "Média", "Alta"], key="simul2")
    ocupacao = st.slider("Taxa de Ocupação (%)", 0, 100, 70)
    if unidade_sim and not precos.empty:
        preco = precos[(precos["nome"] == unidade_sim) & (precos["temporada"] == temporada_sim)]["preco_base"]
        if not preco.empty:
            valor_sim = float(preco.values[0]) * (ocupacao / 100)
            st.info(f"Valor simulado para {unidade_sim} ({temporada_sim}): R$ {valor_sim:,.2f}")
        else:
            st.warning("Não há preço base cadastrado para essa combinação.")

# ============== OUTRAS ABAS =========================
elif aba == "Parâmetros do Sistema":
    st.info("Em breve: configurações gerais do sistema.")
elif aba == "Exportar/Importar Dados":
    st.info("Em breve: funcionalidade de exportar e importar dados.")
elif aba == "Sobre o Sistema":
    st.markdown("""
    ## 🛠 Sobre o Sistema  
    Desenvolvido por **Alex Oliveira**.  
    Versão: **4.0 (mobile)**  
    Aplicação para gestão completa de hospedagens.
    """)
