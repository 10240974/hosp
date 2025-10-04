# app.py
import os
import re
import unicodedata
from datetime import date, timedelta
from calendar import monthrange  # último dia do mês

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import sqlite3
import streamlit as st
import threading
import time
import requests
import urllib.parse  # ADICIONADO: usado para montar mailto/whatsapp

# ============== CONFIGURAÇÃO DA PÁGINA ==============
# Configuração da página para abrir com menu lateral fechado
st.set_page_config(page_title="Hospedar", layout="wide", initial_sidebar_state="collapsed")

# Adicionar estilo para reduzir o espaçamento no topo
st.markdown(
    """
    <style>
    .block-container {
        padding-top: 1rem; /* Reduz o espaçamento superior */
    }
    </style>
    """,
    unsafe_allow_html=True
)

# Adicionar estilo moderno inspirado no Mercado Pago
st.markdown(
    """
    <style>
    /* Fonte moderna */
    @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&display=swap');
    html, body, [class*="css"] {
        font-family: 'Roboto', sans-serif;
    }

    /* Cor de fundo */
    .block-container {
        background-color: #f5f7fa;
        padding: 2rem;
        border-radius: 10px;
    }

    /* Títulos */
    h1, h2, h3 {
        color: #0057e7;
        font-weight: 700;
    }

    /* Botões */
    button[kind="primary"] {
        background-color: #0057e7 !important;
        color: white !important;
        border: none !important;
        border-radius: 8px !important;
        padding: 0.5rem 1rem !important;
        font-weight: 500 !important;
    }
    button[kind="primary"]:hover {
        background-color: #0046c0 !important;
    }

    /* Inputs */
    input, select, textarea {
        border: 1px solid #d1d5db !important;
        border-radius: 8px !important;
        padding: 0.5rem !important;
    }

    /* Tabelas */
    .dataframe {
        border: 1px solid #e5e7eb;
        border-radius: 8px;
        overflow: hidden;
    }
    .dataframe th {
        background-color: #0057e7;
        color: white;
        font-weight: 500;
        padding: 0.5rem;
    }
    .dataframe td {
        padding: 0.5rem;
    }

    /* Sidebar */
    .sidebar .sidebar-content {
        background-color: #0057e7;
        color: white;
    }
    .sidebar .sidebar-content a {
        color: white !important;
    }
    .sidebar .sidebar-content a:hover {
        color: #d1d5db !important;
    }

    /* Métricas */
    .stMetric {
        background-color: #ffffff;
        border: 1px solid #e5e7eb;
        border-radius: 8px;
        padding: 1rem;
        box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
    }
    </style>
    """,
    unsafe_allow_html=True
)

# ============== BANCO DE DADOS ======================

def conectar():
    return sqlite3.connect("hospedagem.db", check_same_thread=False)

# Atualizar a tabela de unidades no banco de dados (com migração)
def inicializar_db():
    conn = conectar()
    c = conn.cursor()

    # Tabelas base (sem colunas novas em 'unidades' aqui, para permitir migração)
    c.execute("""
        CREATE TABLE IF NOT EXISTS unidades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT,
            localizacao TEXT,
            capacidade INTEGER,
            status TEXT
        )
    """)
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
    c.execute("""
        CREATE TABLE IF NOT EXISTS precos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            unidade_id INTEGER,
            temporada TEXT,
            preco_base REAL,
            FOREIGN KEY(unidade_id) REFERENCES unidades(id)
        )
    """)

    # --- MIGRAÇÃO: garante colunas novas em 'unidades' ---
    c.execute("PRAGMA table_info(unidades)")
    cols = {row[1] for row in c.fetchall()}  # nomes das colunas existentes

    if "administracao" not in cols:
        c.execute("ALTER TABLE unidades ADD COLUMN administracao TEXT DEFAULT 'Não'")
    if "percentual_administracao" not in cols:
        c.execute("ALTER TABLE unidades ADD COLUMN percentual_administracao REAL DEFAULT 0.0")

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
    """Retorna (noites_ocupadas, taxa_ocupacao%).
    Day-use (checkin >= checkout) conta 1 noite no dia do check-in."""
    if locacoes_df.empty or inicio > fim:
        return 0, 0.0

    noites_total, noites_ocupadas = 0, 0
    unidades_ids = locacoes_df["unidade_id"].unique().tolist()
    for uid in unidades_ids:
        dias_janela = pd.date_range(inicio, fim, freq="D")
        noites_total += len(dias_janela)
        locs = locacoes_df[locacoes_df["unidade_id"] == uid]
        for _, loc in locs.iterrows():
            ci = pd.to_datetime(loc["checkin"]).date()
            co = pd.to_datetime(loc["checkout"]).date()
            if ci >= co:
                if inicio <= ci <= fim:
                    noites_ocupadas += 1
            else:
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
st.sidebar.title("Menu Principal")

# Toggle mobile
MOBILE = st.sidebar.toggle("📱 Modo Mobile", value=True, help="Ativa interface compacta")

menu_principal = st.sidebar.radio(
    "",
    ["🏠 Dashboard", "📊 Relatórios", "🗂 Dados Cadastrais", "⚙️ Configurações"]
)

if menu_principal == "🏠 Dashboard":
    aba = "Dashboard de Ocupação"
elif menu_principal == "📊 Relatórios":
    aba = st.sidebar.radio(
        "📈 Tipo de Relatório",
        ["Relatório de Despesas", "Análise de Receita e Lucro", "Noites Reservadas", "Administradora", "Relatório de Ganhos Anuais"]
    )
elif menu_principal == "🗂 Dados Cadastrais":
    aba = st.sidebar.radio("📁 Dados Cadastrais", ["Cadastro de Unidades", "Locações", "Despesas", "Precificação"])
else:
    aba = st.sidebar.radio("🔧 Opções do Sistema", ["Parâmetros do Sistema", "Exportar/Importar Dados", "Sobre o Sistema"])

# ============== DASHBOARD ===========================
if aba == "Dashboard de Ocupação":
    # Título do dashboard
    st.markdown(
        """
        <h2 style="font-size:24px; color:black; font-weight:400; margin-bottom:1rem;">
            🏠 Ocupação - Visão Geral
        </h2>
        """,
        unsafe_allow_html=True
    )

    # Botão para exibir/ocultar filtros
    with st.expander("🔍 Filtros", expanded=False):
        st.subheader("Filtros")
        unidades_dash = get_unidades()
        locacoes_dash = get_locacoes()
        despesas_dash = get_despesas()

        # Filtros de Ano e Mês
        st.subheader("Filtro de Período")
        col1, col2 = st.columns(2)
        with col1:
            anos = sorted(set(pd.to_datetime(locacoes_dash["checkin"]).dt.year.dropna().unique()))
            ano_sel = st.selectbox("Ano", anos, index=len(anos) - 1)
        with col2:
            # Adicionar "Todos os Meses" como opção
            meses_opts = ["Todos"] + list(range(1, 13))
            mes_sel = st.selectbox(
                "Selecione o Mês",
                meses_opts,
                format_func=lambda x: "Todos os Meses" if x == "Todos" else f"{x:02} - {['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'][x-1]}"
            )

            # Ajustar a lógica para considerar todos os meses quando "Todos" for selecionado
            if mes_sel == "Todos":
                meses_filtrados = list(range(1, 13))  # Todos os meses
            else:
                meses_filtrados = [mes_sel]

        # Calcular data_inicio e data_fim com base no ano e mês selecionados
        if mes_sel == "Todos":
            data_inicio = date(ano_sel, 1, 1)  # Primeiro dia do ano
            data_fim = date(ano_sel, 12, 31)  # Último dia do ano
        else:
            data_inicio = date(ano_sel, mes_sel, 1)  # Primeiro dia do mês selecionado
            ultimo_dia = monthrange(ano_sel, mes_sel)[1]  # Último dia do mês selecionado
            data_fim = date(ano_sel, mes_sel, ultimo_dia)

        # Filtrar locações e despesas pelo ano e mês selecionados
        if  mes_sel == "Todos":
            locacoes_dash = locacoes_dash[
                pd.to_datetime(locacoes_dash["checkin"]).dt.year == ano_sel
            ]
            despesas_dash = despesas_dash[
                pd.to_datetime(despesas_dash["data"]).dt.year == ano_sel
            ]
        else:
            locacoes_dash = locacoes_dash[
                (pd.to_datetime(locacoes_dash["checkin"]).dt.year == ano_sel) &
                (pd.to_datetime(locacoes_dash["checkin"]).dt.month.isin(meses_filtrados))
            ]
            despesas_dash = despesas_dash[
                (pd.to_datetime(despesas_dash["data"]).dt.year == ano_sel) &
                (pd.to_datetime(despesas_dash["data"]).dt.month.isin(meses_filtrados))
            ]

        # Filtro de unidades (adiciona multiselect)
        unidades_opts = sorted(unidades_dash["nome"].unique().tolist())
        unidades_sel = st.multiselect("Unidades", unidades_opts, default=unidades_opts)

        # Filtra unidades selecionadas
        if unidades_sel:
            unidades_ids_sel = unidades_dash[unidades_dash["nome"].isin(unidades_sel)]["id"].tolist()
            locacoes_dash = locacoes_dash[locacoes_dash["unidade_id"].isin(unidades_ids_sel)]
            despesas_dash = despesas_dash[despesas_dash["unidade_id"].isin(unidades_ids_sel)]

    # ====== Cards Mobile (resumo) ======
    if MOBILE is not None:
        receita_periodo = 0.0
        despesas_periodo = 0.0
        lucro = 0.0
        noites_ocup, taxa = 0, 0.0

        # Receita no período (com day-use)
        if not locacoes_dash.empty:
            for _, loc in locacoes_dash.iterrows():
                ci = pd.to_datetime(loc["checkin"]).date()
                co = pd.to_datetime(loc["checkout"]).date()
                val = float(loc.get("valor") or 0.0)

                if ci >= co:
                    # day-use: 1 diária no dia do check-in se dentro da janela
                    if data_inicio <= ci <= data_fim:
                        receita_periodo += val
                else:
                    noites_totais = (co - ci).days
                    if noites_totais > 0:
                        noites_no_periodo = pd.date_range(
                            max(ci, data_inicio), min(co, data_fim) - pd.Timedelta(days=1), freq="D"
                        )
                        receita_periodo += (val / noites_totais) * len(noites_no_periodo)

        # Despesa no período
        if not despesas_dash.empty:
            d = despesas_dash[
                (pd.to_datetime(despesas_dash["data"]).dt.date >= data_inicio) &
                (pd.to_datetime(despesas_dash["data"]).dt.date <= data_fim)
            ]
            if not unidades_sel:  # Verifica se há unidades selecionadas
                despesas_periodo = float(d["valor"].sum()) if not d.empty else 0.0
            else:
                d = d.merge(unidades_dash[["id", "nome"]], left_on="unidade_id", right_on="id", how="left")
                d = d[d["nome"].isin(unidades_sel)]
                despesas_periodo = float(d["valor"].sum()) if not d.empty else 0.0

        lucro = receita_periodo - despesas_periodo
        noites_ocup, taxa = resumo_ocupacao(locacoes_dash, data_inicio, data_fim)

        # Exibir os cards
        c1, c2 = st.columns(2)
        card(c1, "💰 Receita período", f"R$ {receita_periodo:,.2f}")
        card(c2, "💸 Despesas período", f"R$ {despesas_periodo:,.2f}")
        c3, c4 = st.columns(2)
        card(c3, "📈 Lucro líquido", f"R$ {lucro:,.2f}")
        card(c4, "🏨 Ocupação", f"{taxa:.1f}%" " -      " f"{noites_ocup} noites")

        # ====== Tabela calendário (desktop/overview) ======
        dias_periodo = pd.date_range(start=data_inicio, end=data_fim, freq="D")[::-1]
        dias_str = [d.strftime("%d/%m") for d in dias_periodo]

        # Filtra unidades para tabela (NÃO adiciona "Administração" como linha de unidade)
        unidades_dash_filtrado = unidades_dash[unidades_dash["nome"].isin(unidades_sel)] if unidades_sel else unidades_dash

        index_nomes = (
            unidades_dash_filtrado["nome"].tolist() if not unidades_dash_filtrado.empty else []
        ) + ["Total R$"]

        valores_num = pd.DataFrame(0.0, index=index_nomes, columns=dias_str)
        tabela_icon = pd.DataFrame("", index=index_nomes, columns=dias_str)

        if not unidades_dash_filtrado.empty and not locacoes_dash.empty:
            for _, unidade in unidades_dash_filtrado.iterrows():
                locs = locacoes_dash[locacoes_dash["unidade_id"] == unidade["id"]]
                for _, loc in locs.iterrows():
                    checkin = pd.to_datetime(loc["checkin"]).date()
                    checkout = pd.to_datetime(loc["checkout"]).date()
                    valor = float(loc.get("valor", 0) or 0)

                    # ---- DAY-USE: conta 1 diária no dia do check-in ----
                    if checkin >= checkout:
                        dias_locados = [checkin]
                    else:
                        dr = pd.date_range(checkin, checkout - pd.Timedelta(days=1), freq="D").to_pydatetime()
                        dias_locados = [d.date() for d in dr]

                    num_diarias = max(1, len(dias_locados))
                    valor_dia = (valor / num_diarias) if num_diarias > 0 else 0.0

                    # Marca ocupação e soma valor
                    for d in dias_locados:
                        dia_str = d.strftime("%d/%m")
                        if dia_str in dias_str:
                            tabela_icon.loc[unidade["nome"], dia_str] = "🟧"
                            valores_num.loc[unidade["nome"], dia_str] += valor_dia

                    # Check-in / Check-out (sem sobrescrever 🟧)
                    if data_inicio <= checkin <= data_fim:
                        dia_checkin = checkin.strftime("%d/%m")
                        if dia_checkin in dias_str and tabela_icon.loc[unidade["nome"], dia_checkin] == "":
                            tabela_icon.loc[unidade["nome"], dia_checkin] = "🟦"
                    if data_inicio <= checkout <= data_fim:
                        dia_checkout = checkout.strftime("%d/%m")
                        if dia_checkout in dias_str and tabela_icon.loc[unidade["nome"], dia_checkout] == "":
                            tabela_icon.loc[unidade["nome"], dia_checkout] = "◧"

        # Ocupação diária (evita divisão por zero)
        denom = max(1, len(unidades_dash_filtrado))
        ocupacao_diaria = (tabela_icon.apply(lambda col: col.value_counts().get("🟧", 0), axis=0) / denom) * 100

        tabela_visual = tabela_icon.copy()
        for extra_col in ["Total R$", "Valor Líquido (-13%)", "Total Administradora"]:
            if extra_col not in tabela_visual.columns:
                tabela_visual[extra_col] = ""

        tabela_visual.loc["Ocupação (%)"] = ocupacao_diaria.map(lambda v: f"{v:.1f}%")

        # Totais diários apenas das unidades (exclui a linha 'Total R$')
        linhas_base = [idx for idx in valores_num.index if idx != "Total R$"]
        valores_num.loc["Total R$", dias_str] = valores_num.loc[linhas_base, dias_str].sum(axis=0)

        # Totais por linha (mês)
        valores_num["Total R$"] = valores_num[dias_str].sum(axis=1)
        valores_num["Valor Líquido (-13%)"] = valores_num["Total R$"] * 0.87

        # -------- Coluna "Total Administradora" (por unidade, com % próprio) --------
        admin_col = pd.Series(0.0, index=valores_num.index, dtype=float)
        if not unidades_dash_filtrado.empty:
            meta = unidades_dash_filtrado.set_index("nome")
            for unit_name in meta.index:
                admin_flag = str(meta.at[unit_name, "administracao"]) if "administracao" in meta.columns else "Não"
                raw_pct = meta.at[unit_name, "percentual_administracao"] if "percentual_administracao" in meta.columns else 0.0
                try:
                    pct = float(raw_pct)
                except Exception:
                    pct = 0.0
                if pd.isna(pct):
                    pct = 0.0

                if admin_flag == "Sim" and pct > 0:
                    admin_col[unit_name] = valores_num.at[unit_name, "Total R$"] * (pct / 100.0)
                else:
                    admin_col[unit_name] = 0.0

        # A linha "Total R$" recebe a soma das administrações das unidades
        admin_col["Total R$"] = float(admin_col.drop(labels=["Total R$"], errors="ignore").sum())
        valores_num["Total Administradora"] = admin_col
        # ---------------------------------------------------------------------------

        # Monta visual com valores formatados
        for r in tabela_icon.index:
            for c in dias_str:
                v = float(valores_num.loc[r, c])
                icone = tabela_icon.loc[r, c]
                tabela_visual.loc[r, c] = f"{icone} {v:,.2f}".strip() if v > 0 else icone

        tabela_visual["Total R$"] = valores_num["Total R$"].map(lambda v: f"{v:,.2f}")
        tabela_visual["Valor Líquido (-13%)"] = valores_num["Valor Líquido (-13%)"].map(lambda v: f"{v:,.2f}")
        tabela_visual["Total Administradora"] = valores_num["Total Administradora"].map(lambda v: f"{v:,.2f}")

        tabela_visual = tabela_visual[dias_str + ["Total R$", "Valor Líquido (-13%)", "Total Administradora"]]

        st.markdown(f"**Ocupação Geral ({data_inicio.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')})**")
        st.dataframe(tabela_visual, use_container_width=True)

        # Legenda ajustada
        st.markdown(
            """
            <div style="font-size:12px; color:gray; margin-top:1px;">
                <strong>Legenda:</strong><br>
                🟧 Ocupado o dia todo (com valor)<br>
                🟦 Check-in (após 14h)<br>
                ◧ Check-out (até 11h — sem valor)
            </div>
            """,
            unsafe_allow_html=True
        )
    else:
        st.info("A tabela está disponível apenas no modo Mobile.")

    # ====== Próximos movimentos ======
    st.markdown("### 📅 Próximos movimentos (7 dias)")
    locacoes_dash = get_locacoes()  # garantir que está definido
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

# =========================
#  RELATÓRIO: NOITES POR DIA
# =========================
if aba == "Noites Reservadas":
    st.header("Noites Reservadas por Mês")

    # Carrega dados
    unidades_df = get_unidades()
    locacoes_df = get_locacoes()

    if unidades_df.empty or locacoes_df.empty:
        st.info("Cadastre unidades e locações para visualizar este relatório.")
    else:
        # Junta para ter o nome da unidade
        loc = locacoes_df.merge(unidades_df, left_on="unidade_id", right_on="id", suffixes=("", "_u"))

        # Converte datas
        loc["checkin"] = pd.to_datetime(loc["checkin"], errors="coerce")
        loc["checkout"] = pd.to_datetime(loc["checkout"], errors="coerce")
        loc = loc.dropna(subset=["checkin", "checkout"])

        # Expande reserva em noites (cada dia entre checkin e checkout-1)
        registros = []
        for _, r in loc.iterrows():
            ci = r["checkin"].date()
            co = r["checkout"].date()
            if ci >= co:
                continue
            nights = pd.date_range(ci, co - pd.Timedelta(days=1), freq="D")
            for d in nights:
                registros.append({"data_noite": d.date()})

        if not registros:
            st.info("Não há noites reservadas para o período atual dos dados.")
        else:
            nights_df = pd.DataFrame(registros)
            nights_df["ano"] = pd.to_datetime(nights_df["data_noite"]).dt.year
            nights_df["mes_num"] = pd.to_datetime(nights_df["data_noite"]).dt.month

            # Filtros
            anos = sorted(nights_df["ano"].unique().tolist())
            col_f1, col_f2 = st.columns([1, 3])
            with col_f1:
                ano_sel = st.selectbox("Ano", anos, index=len(anos) - 1)

            df_f = nights_df[(nights_df["ano"] == ano_sel)]

            # Agrupa por mês
            agg = df_f.groupby("mes_num").size().reset_index(name="noites")

            # Meses (PT-BR)
            mes_label = {1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun",
                         7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez"}
            agg["mes"] = agg["mes_num"].map(mes_label)
            ordem_meses = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

            # Gráfico
            fig = px.bar(
                agg,
                x="mes",
                y="noites",
                category_orders={"mes": ordem_meses},
                labels={"mes": "Mês", "noites": "Noites"},
                title=f"Noites Reservadas por Mês • {ano_sel}",
                text="noites",
            )
            fig.update_layout(xaxis_title="Mês", yaxis_title="Noites")
            fig.update_traces(textposition="outside", cliponaxis=False)

            st.plotly_chart(fig, use_container_width=True)

            # Tabela (opcional)
            with st.expander("Ver tabela agregada"):
                tabela = agg.pivot_table(index="mes", values="noites", aggfunc="sum")
                tabela = tabela.reindex(ordem_meses)
                st.dataframe(tabela.fillna(0).astype(int), use_container_width=True)

# ============== RELATÓRIO DE DESPESAS ==============
elif aba == "Relatório de Despesas":
    st.header("Despesas por Mês e Tipo")

    unidades_df = get_unidades()
    despesas_df = get_despesas()

    if unidades_df.empty or despesas_df.empty:
        st.info("Cadastre unidades e despesas para visualizar este relatório.")
    else:
        # Merge despesas com unidades para ter nome
        des = despesas_df.merge(unidades_df, left_on="unidade_id", right_on="id", suffixes=("", "_u"))
        des["data"] = pd.to_datetime(des["data"], errors="coerce")
        des = des.dropna(subset=["data"])
        des["ano"] = des["data"].dt.year
        des["mes_num"] = des["data"].dt.month
        des["nome_mes"] = des["mes_num"].map({
            1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun",
            7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez"
        })

        # ---- Filtros ----
        anos = sorted(des["ano"].unique())
        c1, c2 = st.columns([1, 3])
        with c1:
            ano_sel = st.selectbox("Ano", anos, index=len(anos) - 1)
        with c2:
            tipos_opts = sorted(des["tipo"].dropna().unique()) if "tipo" in des.columns else []
            tipo_sel = st.multiselect("Tipo de Despesa", tipos_opts, default=tipos_opts)

        # Aplicar filtros
        df_f = des[des["ano"] == ano_sel].copy()
        if tipo_sel:
            df_f = df_f[df_f["tipo"].isin(tipo_sel)]

        # Agregar por mês e tipo
        agg_df = df_f.groupby(["nome_mes", "tipo"], as_index=False)["valor"].sum()

        if agg_df.empty:
            st.warning("Não há despesas para os filtros selecionados.")
        else:
            # Gráfico de barras
            fig = px.bar(
                agg_df,
                x="nome_mes",
                y="valor",
                color="tipo",
                category_orders={"nome_mes": ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]},
                labels={"valor": "Valor (R$)", "nome_mes": "Mês", "tipo": "Tipo de Despesa"},
                title=f"Despesas por Mês e Tipo - {ano_sel}"
            )
            fig.update_layout(barmode="stack", height=600)
            st.plotly_chart(fig, use_container_width=True)

            # Mostrar tabela
            with st.expander("Ver tabela detalhada"):
                st.dataframe(agg_df, use_container_width=True)

elif aba == "Análise de Receita e Lucro":
    st.header("Análise de Receita x Despesa com Lucro (por mês).")

    unidades_df = get_unidades()
    locacoes_df = get_locacoes()
    despesas_df = get_despesas()

    if unidades_df.empty or (locacoes_df.empty and despesas_df.empty):
        st.info("Cadastre unidades, locações e despesas para visualizar este relatório.")
    else:
        # Filtrar unidades com status diferente de "Manutenção"
        unidades_df = unidades_df[unidades_df["status"] != "Manutenção"]

        # ---- Preparo base: juntar nome da unidade em locações e despesas ----
        if not locacoes_df.empty:
            loc = locacoes_df.merge(unidades_df, left_on="unidade_id", right_on="id", suffixes=("", "_u"))
            loc["checkin"] = pd.to_datetime(loc["checkin"], errors="coerce")
            loc = loc.dropna(subset=["checkin"])
            loc["ano"] = loc["checkin"].dt.year
            loc["mes_num"] = loc["checkin"].dt.month
            loc["nome_unidade"] = loc["nome"]
        else:
            loc = pd.DataFrame(columns=["nome_unidade", "ano", "mes_num", "valor"])

        if not despesas_df.empty:
            des = despesas_df.merge(unidades_df, left_on="unidade_id", right_on="id", suffixes=("", "_u"))
            des["data"] = pd.to_datetime(des["data"], errors="coerce")
            des = des.dropna(subset=["data"])
            des["ano"] = des["data"].dt.year
            des["mes_num"] = des["data"].dt.month
            des["nome_unidade"] = des["nome"]
        else:
            des = pd.DataFrame(columns=["nome_unidade", "ano", "mes_num", "valor"])

        # ---- Filtros (Ano + Unidades) ----
        anos_loc = loc["ano"].unique().tolist() if not loc.empty else []
        anos_des = des["ano"].unique().tolist() if not des.empty else []
        anos = sorted(set(anos_loc + anos_des))
        if not anos:
            st.info("Não há dados de anos para agrupar.")
        else:
            c1, c2 = st.columns([1, 3])
            with c1:
                ano_sel = st.selectbox("Ano", anos, index=len(anos) - 1)
            with c2:
                unidades_opts = sorted(unidades_df["nome"].unique().tolist())
                unidades_sel = st.multiselect("Unidades", unidades_opts, default=unidades_opts)

            # Aplica filtros
            loc_f = loc[loc["ano"] == ano_sel].copy()
            des_f = des[des["ano"] == ano_sel].copy()
            if unidades_sel:
                loc_f = loc_f[loc_f["nome_unidade"].isin(unidades_sel)]
                des_f = des_f[des_f["nome_unidade"].isin(unidades_sel)]

            # ---- Agregações por mês ----
            receita_m = (
                loc_f.groupby("mes_num")["valor"].sum().rename("Receita").reset_index()
                if not loc_f.empty else pd.DataFrame({"mes_num": [], "Receita": []})
            )
            despesa_m = (
                des_f.groupby("mes_num")["valor"].sum().rename("Despesa").reset_index()
                if not des_f.empty else pd.DataFrame({"mes_num": [], "Despesa": []})
            )

            # Grade completa Jan..Dez para mostrar zeros
            base_meses = pd.DataFrame({"mes_num": list(range(1, 12 + 1))})
            dfm = base_meses.merge(receita_m, on="mes_num", how="left") \
                            .merge(despesa_m, on="mes_num", how="left")
            dfm["Receita"] = dfm["Receita"].fillna(0.0)
            dfm["Despesa"] = dfm["Despesa"].fillna(0.0)
            dfm["Lucro"] = dfm["Receita"] - dfm["Despesa"]

            mes_label = {1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun",
                         7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez"}
            ordem_meses = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
            dfm["Mês"] = dfm["mes_num"].map(mes_label)
            dfm = dfm.sort_values("mes_num")

            # ---- Gráfico combinado (barras + linha) ----
            fig = go.Figure()

            # Barras: Receita
            fig.add_trace(go.Bar(x=dfm["Mês"], y=dfm["Receita"], name="Receita"))
            # Barras: Despesa
            fig.add_trace(go.Bar(x=dfm["Mês"], y=dfm["Despesa"], name="Despesa"))
            # Linha: Lucro (eixo secundário)
            fig.add_trace(go.Scatter(x=dfm["Mês"], y=dfm["Lucro"], name="Lucro", mode="lines+markers", yaxis="y2"))

            fig.update_layout(
                title=f"Receita x Despesa (barras) e Lucro (linha) • {ano_sel}",
                xaxis=dict(title="Mês", categoryorder="array", categoryarray=ordem_meses),
                yaxis=dict(title="Valor (R$)"),
                yaxis2=dict(title="Lucro (R$)", overlaying="y", side="right"),
                barmode="group",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                margin=dict(l=40, r=40, t=60, b=40),
            )

            st.plotly_chart(fig, use_container_width=True)

            # Tabela resumo (opcional)
            with st.expander("Ver tabela mensal"):
                tabela = dfm[["Mês", "Receita", "Despesa", "Lucro"]].copy()
                st.dataframe(tabela, use_container_width=True)
elif aba == "Administradora":
    st.header("Relatório para Administradora")
    
    # Carregar dados
    unidades_df = get_unidades()
    locacoes_df = get_locacoes()

    if unidades_df.empty or locacoes_df.empty:
        st.info("Cadastre unidades e locações para visualizar este relatório.")
    else:
        # Filtrar unidades com Administração = "Sim"
        unidades_admin = unidades_df[unidades_df["administracao"] == "Sim"]

        # Merge locações com unidades para obter nome e % administração
        loc = locacoes_df.merge(
            unidades_admin[["id", "nome", "administracao", "percentual_administracao"]],
            left_on="unidade_id", right_on="id", how="left", suffixes=("", "_u")
        )

        # Converte datas
        loc["checkin"] = pd.to_datetime(loc["checkin"], errors="coerce").dt.date
        loc["checkout"] = pd.to_datetime(loc["checkout"], errors="coerce").dt.date
        loc = loc.dropna(subset=["checkin", "checkout"])

        # ----- Filtros -----
        anos = sorted(set([d.year for d in loc["checkin"]] + [d.year for d in loc["checkout"]]))
        plataformas_disponiveis = sorted(loc["plataforma"].dropna().unique())  # Plataformas disponíveis
        col1, col2, col3, col4 = st.columns([1, 1, 2, 2])
        with col1:
            ano_sel = st.selectbox("Ano", anos, index=len(anos) - 1)
        with col2:
            meses_opts = list(range(1, 12 + 1))
            mes_sel = st.selectbox("Mês", ["Todos"] + meses_opts, format_func=lambda x: "Todos" if x == "Todos" else f"{x:02}")
        with col3:
            unidades_opts = sorted(unidades_admin["nome"].dropna().unique().tolist())
            unidades_sel = st.multiselect("Unidades", unidades_opts, default=unidades_opts)
        with col4:
            plataformas_sel = st.multiselect("Plataformas", ["Todas"] + plataformas_disponiveis, default=["Todas"])

        # Período alvo
        if mes_sel == "Todos":
            period_start = date(ano_sel, 1, 1)
            period_end = date(ano_sel, 12, 31)
            periodo_str = f"{ano_sel}"
            nome_mes = "Todos"
        else:
            last_day = monthrange(ano_sel, mes_sel)[1]
            period_start = date(ano_sel, mes_sel, 1)
            period_end = date(ano_sel, mes_sel, last_day)
            periodo_str = f"{mes_sel:02}/{ano_sel}"
            nome_mes = f"{mes_sel:02}"

        # Mantém apenas reservas cujo checkout está no período selecionado
        loc_f = loc[(loc["checkout"] >= period_start) & (loc["checkout"] <= period_end)].copy()

        # Aplicar filtros adicionais
        if unidades_sel:
            loc_f = loc_f[loc_f["nome"].isin(unidades_sel)]
        if "Todas" not in plataformas_sel:
            loc_f = loc_f[loc_f["plataforma"].isin(plataformas_sel)]

        if loc_f.empty:
            st.warning("Não há dados para os filtros selecionados.")
        else:
            # ---- Cálculos por reserva (linhas da tabela) ----
            def noites_no_periodo(ci: date, co: date) -> int:
                # day-use: checkin >= checkout conta 1 se o check-in cair no período
                if ci >= co:
                    return 1 if (period_start <= ci <= period_end) else 0
                ini = max(ci, period_start)
                fim = min(co, period_end)
                return max(0, (fim - ini).days)

            def valor_periodo(row) -> float:
                ci, co = row["checkin"], row["checkout"]
                total = float(row.get("valor") or 0.0)
                total_noites = 1 if ci >= co else max(1, (co - ci).days)
                v_dia = total / total_noites
                return v_dia * noites_no_periodo(ci, co)

            def valor_adm(row, v_liquido) -> float:
                flag = str(row.get("administracao", "Não"))
                pct = row.get("percentual_administracao", 0.0)
                try:
                    pct = float(pct)
                except Exception:
                    pct = 0.0
                if pd.isna(pct):
                    pct = 0.0

                return v_liquido * (pct / 100.0) if (flag == "Sim" and pct > 0) else 0.0

            loc_f["Qtde de Noites"] = loc_f.apply(lambda r: noites_no_periodo(r["checkin"], r["checkout"]), axis=1)
            loc_f["Valor total bruto"] = loc_f.apply(valor_periodo, axis=1)
            loc_f["Valor total líquido"] = loc_f["Valor total bruto"] * 0.87  # Subtraindo 13%
            loc_f["Valor administração"] = loc_f.apply(lambda r: valor_adm(r, r["Valor total líquido"]), axis=1)

            # Monta a tabela final
            tabela = loc_f.rename(columns={
                "nome": "Unidade",
                "checkin": "Check-in",
                "checkout": "Check-out",
                "plataforma": "Plataforma",
                "hospede": "Hóspede"
            })
            tabela = tabela[[
                "Unidade", "Hóspede", "Plataforma", "Check-in", "Check-out",
                "Qtde de Noites", "Valor total bruto", "Valor total líquido", "Valor administração"
            ]]
            tabela = tabela.sort_values(["Unidade", "Check-in", "Check-out"]).reset_index(drop=True)

            # Totais do período 
            tot_noites = int(tabela["Qtde de Noites"].sum())
            tot_valor_bruto = float(tabela["Valor total bruto"].sum())
            tot_valor_liquido = float(tabela["Valor total líquido"].sum())
            tot_adm = float(tabela["Valor administração"].sum())
            st.caption(f"Totais no período — Noites: {tot_noites} • Valor Bruto: R$ {tot_valor_bruto:,.2f} • Valor Líquido: R$ {tot_valor_liquido:,.2f} • Administração: R$ {tot_adm:,.2f}")

            # Adicionar linha de totais
            totais = {
                "Unidade": "Total",
                "Hóspede": "",
                "Plataforma": "",
                "Check-in": "",
                "Check-out": "",
                "Qtde de Noites": tabela["Qtde de Noites"].sum(),
                "Valor total bruto": tabela["Valor total bruto"].sum(),
                "Valor total líquido": tabela["Valor total líquido"].sum(),
                "Valor administração": tabela["Valor administração"].sum(),
            }
            tabela = pd.concat([tabela, pd.DataFrame([totais])], ignore_index=True)

            # Exibir com formatação monetária
            tabela_fmt = tabela.copy()
            tabela_fmt["Valor total bruto"] = tabela_fmt["Valor total bruto"].map(lambda v: f"R$ {v:,.2f}" if pd.notna(v) else "")
            tabela_fmt["Valor total líquido"] = tabela_fmt["Valor total líquido"].map(lambda v: f"R$ {v:,.2f}" if pd.notna(v) else "")
            tabela_fmt["Valor administração"] = tabela_fmt["Valor administração"].map(lambda v: f"R$ {v:,.2f}" if pd.notna(v) else "")
            st.subheader(f"Resumo por Reserva (período: {periodo_str})")
            st.dataframe(tabela_fmt, use_container_width=True)

            # --------- Geração de mensagem (WhatsApp / E-mail) ---------
            st.subheader("Enviar por WhatsApp / E-mail")
            colw1, colw2, colw3 = st.columns([2, 2, 1])
            with colw1:
                phone = st.text_input("Telefone WhatsApp (DDI+DDD+Número, só dígitos)", value="")
            with colw2:
                email = st.text_input("E-mail do destinatário", value="")
            with colw3:
                detalhar = st.checkbox("Detalhar reservas", value=False, help="Inclui cada linha da tabela na mensagem")

            def br_money(v: float) -> str:
                return f"R$ {v:,.2f}"

            # Monta a mensagem com base nos dados filtrados
            linhas = [
                f"Relatório da Administradora — Período: {periodo_str}",
                f"Noites: {int(loc_f['Qtde de Noites'].sum())}",


                f"Valor total líquido: {br_money(loc_f['Valor total líquido'].sum())}",
                f"Valor administração: {br_money(loc_f['Valor administração'].sum())}",
            ]

            if detalhar:
                linhas.append("")
                linhas.append("Detalhes por reserva:")
                for _, r in loc_f.iterrows():
                    linhas.append(
                        f"- {r['nome']} | {r['plataforma']} | {r['checkin'].strftime('%d/%m/%Y')}→{r['checkout'].strftime('%d/%m/%Y')} | "
                        f"Noites: {int(r['Qtde de Noites'])} | Valor bruto: {br_money(r['Valor total bruto'])} | "
                        f"Valor líquido: {br_money(r['Valor total líquido'])} | Administração: {br_money(r['Valor administração'])}"
                    )

            msg = "\n".join(linhas)

            cbtn1, cbtn2 = st.columns(2)
            with cbtn1:
                if st.button("Gerar WhatsApp"):
                    if not phone.strip():
                        st.warning("Informe o telefone (apenas dígitos, com DDI). Ex.: 55XXXXXXXXXXX")
                    else:
                        import urllib.parse
                        link_wa = f"https://wa.me/{phone.strip()}?text={urllib.parse.quote(msg)}"
                        st.markdown(f"[Abrir WhatsApp ▶️]({link_wa})")
            with cbtn2:
                    if not email.strip():
                        st.warning("Informe o e-mail do destinatário.")
                    else:
                        subject = f"Relatório Administradora - {periodo_str}"
                        mailto = f"mailto:{email.strip()}?subject={urlparse.quote(subject)}&body={urlparse.quote(msg)}"
                        st.markdown(f"[Abrir cliente de e-mail ✉️]({mailto})")

            # Pré-visualização da mensagem
            with st.expander("Pré-visualizar mensagem"):
                st.text(msg)

# ============== RELATÓRIO DE GANHOS ANUAIS ========================
# ---- Filtros ----
elif aba == "Relatório de Ganhos Anuais":
    st.header("Ganhos e Despesas Anuais por Unidade e Ano")

    # Carregar dados
    unidades_df = get_unidades()
    locacoes_df = get_locacoes()
    despesas_df = get_despesas()

    if unidades_df.empty or (locacoes_df.empty and despesas_df.empty):
        st.info("Cadastre unidades, locações e despesas para visualizar este relatório.")
    else:
        # ---------- BASES ----------
        # Locações + nome da unidade
        locacoes = locacoes_df.merge(
            unidades_df, left_on="unidade_id", right_on="id", suffixes=("", "_u")
        )
        locacoes["checkin"] = pd.to_datetime(locacoes["checkin"], errors="coerce")
        locacoes["checkout"] = pd.to_datetime(loc["checkout"], errors="coerce")
        locacoes = locacoes.dropna(subset=["checkin", "checkout"])
        locacoes["ano"] = locacoes["checkin"].dt.year
        locacoes["mes"] = locacoes["checkin"].dt.month
        locacoes["valor"] = locacoes["valor"].fillna(0.0)

        # Despesas + nome da unidade
        despesas = despesas_df.merge(
            unidades_df, left_on="unidade_id", right_on="id", suffixes=("", "_u")
        )
        despesas["data"] = pd.to_datetime(despesas["data"], errors="coerce")
        despesas = despesas.dropna(subset=["data"])
        despesas["ano"] = despesas["data"].dt.year
        despesas["mes"] = despesas["data"].dt.month
        despesas["valor"] = despesas["valor"].fillna(0.0)

        # ---------- FILTROS ----------
        ano_atual = date.today().year
        anos_loc = locacoes["ano"].unique().tolist() if not locacoes.empty else []
        anos_des = despesas["ano"].unique().tolist() if not despesas.empty else []
        anos = sorted(set(anos_loc + anos_des))

        unidades_opts = sorted(unidades_df["nome"].unique().tolist())

        col1, col2, col3 = st.columns([1, 2, 2])
        with col1:
            anos_sel = st.multiselect(
                "Selecione o(s) Ano(s)",
                anos,
                default=[ano_atual] if ano_atual in anos else anos
            )
        with col2:
            meses_opts = list(range(1, 13))
            meses_sel = st.multiselect(
                "Selecione o(s) Mês(es)",
                ["Todos"] + meses_opts,
                default=["Todos"],
                format_func=lambda x: "Todos os Meses" if x == "Todos" else f"{x:02} - {['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'][x-1]}"
            )
        with col3:
            unidades_sel = st.multiselect(
                "Selecione a(s) Unidade(s)", unidades_opts, default=unidades_opts
            )

        # Aplicar filtro de meses
        if "Todos" in meses_sel:
            meses_filtrados = list(range(1, 13))  # Todos os meses
        else:
            meses_filtrados = meses_sel

        # ---------- BASE ANUAL (FILTRADA POR ANO, MÊS E DATAS FUTURAS) ----------
        hoje = date.today()  # Data atual

        # Filtrar locações para incluir registros futuros
        loc_base = locacoes[
            ((locacoes["ano"].isin(anos_sel)) & (locacoes["mes"].isin(meses_filtrados))) |
            (locacoes["checkin"].dt.date >= hoje)  # Inclui registros com check-in futuro
        ].copy()

        # Filtrar despesas para incluir registros futuros
        desp_base = despesas[
            ((despesas["ano"].isin(anos_sel)) & (despesas["mes"].isin(meses_filtrados))) |
            (despesas["data"].dt.date >= hoje)  # Inclui registros com data futura
        ].copy()

        if unidades_sel:
            loc_base = loc_base[loc_base["nome"].isin(unidades_sel)]
            desp_base = desp_base[desp_base["nome"].isin(unidades_sel)]

        # Agregações por ano e unidade
        ganhos_por_unidade_ano = (
            loc_base.groupby(["nome", "ano"], as_index=False)["valor"]
            .sum()
            .rename(columns={"nome": "Unidade", "ano": "Ano", "valor": "Ganhos (R$)"})
        )
        despesas_por_unidade_ano = (
            desp_base.groupby(["nome", "ano"], as_index=False)["valor"]
            .sum()
            .rename(columns={"nome": "Unidade", "ano": "Ano", "valor": "Despesas (R$)"})
        )

        ganhos_despesas = pd.merge(
            ganhos_por_unidade_ano,
            despesas_por_unidade_ano,
            on=["Unidade", "Ano"],
            how="outer"
        ).fillna(0.0)
        ganhos_despesas["Lucro (R$)"] = (
            ganhos_despesas["Ganhos (R$)"] - ganhos_despesas["Despesas (R$)"]
        )

        # Pivot anual
        tabela_pivot = ganhos_despesas.pivot(
            index="Unidade",
            columns="Ano",
            values=["Ganhos (R$)", "Despesas (R$)", "Lucro (R$)"]
        ).fillna(0.0)

        # Totais por unidade
        tabela_pivot[("Total por Unidade", "Ganhos (R$)")] = tabela_pivot["Ganhos (R$)"].sum(axis=1)
        tabela_pivot[("Total por Unidade", "Despesas (R$)")] = tabela_pivot["Despesas (R$)"].sum(axis=1)
        tabela_pivot[("Total por Unidade", "Lucro (R$)")] = tabela_pivot["Lucro (R$)"].sum(axis=1)

        # Totais gerais por ano
        totais_gerais = tabela_pivot.sum(axis=0).to_frame().T
        totais_gerais.index = ["Total Geral"]
        tabela_pivot = pd.concat([tabela_pivot, totais_gerais])

        st.dataframe(tabela_pivot.style.format("R$ {:,.2f}"), use_container_width=True)

        # ---------- Totais do ano vigente (FILTRADO POR MÊS) ----------
        locacoes_ano_vigente = locacoes[
            (locacoes["ano"] == ano_atual) & (locacoes["mes"].isin(meses_filtrados))
        ]
        despesas_ano_vigente = despesas[
            (despesas["ano"] == ano_atual) & (despesas["mes"].isin(meses_filtrados))
        ]
        ganhos_ano_vigente = locacoes_ano_vigente["valor"].sum()
        despesas_ano_vigente_val = despesas_ano_vigente["valor"].sum()
        lucro_ano_vigente = ganhos_ano_vigente - despesas_ano_vigente_val

        st.subheader(f"Totais do Ano Vigente ({ano_atual})")
        st.metric("Ganhos do Ano", f"R$ {ganhos_ano_vigente:,.2f}")
        st.metric("Despesas do Ano", f"R$ {despesas_ano_vigente_val:,.2f}")
        st.metric("Lucro do Ano", f"R$ {lucro_ano_vigente:,.2f}")

        # ---------- (Opcional) Visão por meses (NÃO altera soma anual) ----------
        if meses_sel and len(meses_sel) < 12:
            loc_mes = loc_base[loc_base["checkin"].dt.month.isin(meses_sel)].copy()
            desp_mes = desp_base[desp_base["data"].dt.month.isin(meses_sel)].copy()

            gd_mes = (
                loc_mes.groupby(["nome", "ano"], as_index=False)["valor"]
                .sum()
                .rename(columns={"nome": "Unidade", "ano": "Ano", "valor": "Ganhos (R$)"})
            )
            dd_mes = (
                desp_mes.groupby(["nome", "ano"], as_index=False)["valor"]
                .sum()
                .rename(columns={"nome": "Unidade", "ano": "Ano", "valor": "Despesas (R$)"})
            )
            vis_mes = pd.merge(gd_mes, dd_mes, on=["Unidade", "Ano"], how="outer").fillna(0.0)
        
            
        else:
            
            vis_mes = ganhos_despesas.copy()

        # Gráfico
        df_long = vis_mes.melt(
            id_vars=["Unidade", "Ano"],
            value_vars=["Ganhos (R$)", "Despesas (R$)"],
            var_name="Tipo",
            value_name="Valor"
        )
        fig = px.bar(
            df_long,
            x="Unidade",
            y="Valor",
            color="Tipo",
            facet_col="Ano",
            barmode="group",
            labels={"Unidade": "Unidade", "Valor": "Valor (R$)", "Tipo": "Tipo"},
            title="Ganhos e Despesas por Unidade e Ano (soma ANUAL completa; filtro de mês apenas na visualização)"
        )
        fig.update_layout(xaxis_title="Unidade", yaxis_title="Valor (R$)", height=600)
        st.plotly_chart(fig, use_container_width=True)

        # Exportar CSV
        csv = tabela_pivot.reset_index().to_csv(index=False, sep=";", encoding="utf-8-sig").encode("utf-8-sig")
        st.download_button(
            label="📥 Baixar Relatório em CSV",
            data=csv,
            file_name="ganhos_despesas_anuais_por_unidade.csv",
            mime="text/csv"
        )
# ============== CADASTRO DE UNIDADES ===============
elif aba == "Cadastro de Unidades":
    st.header("Cadastro e Controle de Unidades")
    with st.form("cad_unidade"):
        nome = st.text_input("Nome da Unidade")
        localizacao = st.text_input("Localização")
        capacidade = st.number_input("Capacidade", min_value=1, max_value=20, value=4)
        status = st.selectbox("Status", ["Disponível", "Ocupado", "Manutenção"])
        administracao = st.selectbox("Possui Administração?", ["Sim", "Não"])
        percentual_administracao = st.number_input(
            "Percentual de Administração (%)", min_value=0.0, max_value=100.0, value=0.0
        ) if administracao == "Sim" else 0.0
        enviar = st.form_submit_button("Cadastrar", use_container_width=MOBILE)
        if enviar and nome:
            try:
                conn = conectar()
                conn.execute(
                    """
                    INSERT INTO unidades (nome, localizacao, capacidade, status, administracao, percentual_administracao)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (nome, localizacao, int(capacidade), status, administracao, float(percentual_administracao))
                )
                conn.commit()
                st.success("Unidade cadastrada!")
            except Exception as e:
                st.error(f"Erro ao cadastrar unidade: {e}")
            finally:
                conn.close()

    st.subheader("Unidades Cadastradas")
    unidades = get_unidades()
    if not unidades.empty:
        edited_df = st.data_editor(
            unidades[["id", "nome", "localizacao", "capacidade", "status", "administracao", "percentual_administracao"]],
            num_rows="dynamic", use_container_width=True, key="editor_unidades"
        )
        if st.button("Salvar Alterações nas Unidades"):
            conn = conectar()
            try:
                for _, row in edited_df.iterrows():
                    conn.execute(
                        """
                        UPDATE unidades
                        SET nome=?, localizacao=?, capacidade=?, status=?, administracao=?, percentual_administracao=?
                        WHERE id=?
                        """,
                        (
                            row["nome"], row["localizacao"], int(row["capacidade"]), row["status"],
                            row["administracao"], float(row["percentual_administracao"]), int(row["id"])
                        )
                    )
                conn.commit()
                st.success("Alterações salvas!")
            except Exception as e:
                st.error(f"Erro ao salvar alterações: {e}")
            finally:
                conn.close()
            # Recarrega os dados atualizados
            unidades = get_unidades()

    st.subheader("Excluir Unidade")
    if not unidades.empty:
        id_excluir = st.selectbox("Selecione o ID da unidade para excluir", unidades["id"])
        if st.button("Excluir Unidade"):
            conn = conectar()
            try:
                conn.execute("DELETE FROM unidades WHERE id=?", (int(id_excluir),))
                conn.commit()
                st.success(f"Unidade {id_excluir} excluída!")
            except Exception as e:
                st.error(f"Erro ao excluir unidade: {e}")
            finally:
                conn.close()

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

    # Obter o ano e o mês corrente
    ano_corrente = date.today().year
    mes_corrente = date.today().month

    # Carregar locações antes de usar
    locacoes = get_locacoes()

    # Adicionar filtros de ano, mês e unidades
    anos_disponiveis = sorted(locacoes["checkout"].apply(lambda x: pd.to_datetime(x).year).unique())
    ano_loca_filtro = st.selectbox("Filtrar por ano", ["Todos"] + anos_disponiveis, index=anos_disponiveis.index(ano_corrente) + 1)
    mes_loca_filtro = st.selectbox(
        "Filtrar por mês de check-out",
        ["Todos"] + [str(m).zfill(2) for m in range(1, 13)],
        index=mes_corrente if "Todos" not in ["Todos"] else 0
    )

    # Adicionar filtro de unidades
    unidades = get_unidades()
    unidades_opcoes = unidades["nome"].tolist() if not unidades.empty else []
    unidades_filtro = st.multiselect("Filtrar por unidades", ["Todas"] + unidades_opcoes, default=["Todas"])

    # Aplicar os filtros
    if not locacoes.empty and not unidades.empty:
        locacoes = locacoes.merge(unidades, left_on="unidade_id", right_on="id", suffixes=("", "_unidade"))
        if ano_loca_filtro != "Todos":
            locacoes = locacoes[pd.to_datetime(locacoes["checkout"]).dt.year == int(ano_loca_filtro)]
        if mes_loca_filtro != "Todos":
            locacoes = locacoes[pd.to_datetime(locacoes["checkout"]).dt.month == int(mes_loca_filtro)]
        if "Todas" not in unidades_filtro:
            locacoes = locacoes[locacoes["nome"].isin(unidades_filtro)]

        if not locacoes.empty:
            # Calcular o total da coluna "valor"
            total_valor = locacoes["valor"].sum()

            # Adicionar uma linha de total ao DataFrame
            total_row = {
                "id": "Total",
                "nome": "",
                "checkin": "",
                "checkout": "",
                "hospede": "",
                "valor": total_valor,
                "plataforma": "",
                "status_pagamento": ""
            }
            locacoes = pd.concat([locacoes, pd.DataFrame([total_row])], ignore_index=True)

        if MOBILE:
            if locacoes.empty:
                st.info("Sem registros para os filtros.")
            else:
                st.dataframe(locacoes[["id", "nome", "checkin", "checkout", "hospede", "valor", "plataforma", "status_pagamento"]], use_container_width=True)
        else:
            edited_df = st.data_editor(
                locacoes[["id", "nome", "checkin", "checkout", "hospede", "valor", "plataforma", "status_pagamento"]],
                num_rows="dynamic", use_container_width=True, key="editor_locacoes"
            )
            if st.button("Salvar Alterações nas Locações"):
                conn = conectar()
                try:
                    for _, row in edited_df.iterrows():
                        conn.execute(
                            "UPDATE locacoes SET checkin=?, checkout=?, hospede=?, valor=?, plataforma=?, status_pagamento=? WHERE id=?",
                            (row["checkin"], row["checkout"], row["hospede"], float(row["valor"]), row["plataforma"], row["status_pagamento"], int(row["id"]))
                        )
                    conn.commit()
                    st.success("Alterações salvas! Recarregue a página para ver os dados atualizados.")
                except Exception as e:
                    st.error(f"Erro ao salvar alterações: {e}")
                finally:
                    conn.close()

            st.subheader("Excluir Locação")
            id_excluir = st.selectbox("Selecione o ID da locação para excluir", locacoes["id"])
            if st.button("Excluir Locação"):
                conn = conectar()
                conn.execute("DELETE FROM locacoes WHERE id=?", (int(id_excluir),))
                conn.commit()
                conn.close()
                st.success(f"Locação {id_excluir} excluída!")
else:
    st.info("Cadastre unidades e locações para visualizar e editar aqui.")

# ============== DESPESAS ============================
if aba == "Despesas":
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
            st.success("Despesa registrado!")

    st.subheader("Despesas Registradas")
    despesas = get_despesas()
    if not despesas.empty and not unidades.empty:
        despesas = despesas.merge(unidades, left_on="unidade_id", right_on="id", suffixes=("", "_u"))

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
                try:
                    for _, row in edited_df.iterrows():
                        conn.execute(
                            "UPDATE despesas SET data=?, tipo=?, valor=?, descricao=? WHERE id=?",
                            (row["data"], row["tipo"], float(row["valor"]), row["descricao"], int(row["id"]))
                        )
                    conn.commit()
                    st.success("Alterações salvas! Recarregue a página para ver os dados atualizados.")
                except Exception as e:
                    st.error(f"Erro ao salvar alterações: {e}")
                finally:
                    conn.close()

        # ------ Importação de Despesas via Excel ------
        st.subheader("Importar Despesas (Excel)")

        modo_import_despesas = st.radio(
            "Modo de importação", ["Acrescentar (append)", "Sobrescrever (limpar antes)"],
            horizontal=True, key="modo_import_despesas"
        )

        excel_file = st.file_uploader("Selecione o arquivo Excel", type=["xlsx", "xls"], key="upload_despesas")
        if excel_file is not None:
            try:
                # Ler o arquivo Excel
                df_excel = pd.read_excel(excel_file, dtype=str)
                df_excel.columns = [c.strip().lower() for c in df_excel.columns]  # Normalizar nomes das colunas
                df_excel = df_excel.applymap(lambda x: x.strip() if isinstance(x, str) else x)

                # Mapear colunas esperadas
                alias = {
                    "unidade": ["unidade", "unit", "nome_unidade", "apto", "apartamento", "imovel", "imóvel"],
                    "data": ["data", "date", "data_despesa"],
                    "tipo": ["tipo", "categoria", "tipo_despesa"],
                    "valor": ["valor", "valor_total", "preco", "preço", "amount", "price"],
                    "descricao": ["descricao", "descrição", "detalhes", "observacao", "observação"]
                }

                def pick(col_alts):
                    for c in col_alts:
                        if c in df_excel.columns:
                            return c
                    return None

                selected = {k: pick(v) for k, v in alias.items()}
                rename_map = {v: k for k, v in selected.items() if v is not None}
                df_excel = df_excel.rename(columns=rename_map)

                # Verificar colunas obrigatórias
                obrigatorias = ["unidade", "data", "tipo", "valor"]
                faltando = [c for c in obrigatorias if c not in df_excel.columns]
                if faltando:
                    st.error(f"Faltam colunas obrigatórias no Excel: {', '.join(faltando)}")
                else:
                    # Converter colunas
                    df_excel["data"] = pd.to_datetime(df_excel["data"], dayfirst=True, errors="coerce").dt.date
                    df_excel["valor"] = parse_valor_series(df_excel["valor"])
                    df_excel["descricao"] = df_excel["descricao"].fillna("")

                    # Exibir prévia dos dados
                    st.dataframe(
                        df_excel[["unidade", "data", "tipo", "valor", "descricao"]].head(20),
                        use_container_width=True, height=360
                    )

                    if st.button("Importar Despesas", key="importar_despesas"):
                        try:
                            unidades_df = get_unidades()
                            if unidades_df.empty:
                                st.error("Não há unidades cadastradas. Cadastre unidades antes de importar despesas.")
                            else:
                                conn = conectar()
                                cur = conn.cursor()

                                if modo_import_despesas == "Sobrescrever (limpar antes)":
                                    cur.execute("DELETE FROM despesas")
                                    conn.commit()

                                mapa_unidade = {_norm(n): int(i) for n, i in zip(unidades_df["nome"], unidades_df["id"])}
                                inseridos, pulados = 0, 0

                                for _, row in df_excel.iterrows():
                                    try:
                                        unidade_id = mapa_unidade.get(_norm(row.get("unidade")))
                                        data = row.get("data")
                                        tipo = row.get("tipo")
                                        valor = float(row.get("valor") or 0.0)
                                        descricao = row.get("descricao", "")

                                        if not unidade_id or pd.isna(data) or not tipo:
                                            pulados += 1
                                            continue

                                        cur.execute(
                                            "INSERT INTO despesas (unidade_id, data, tipo, valor, descricao) VALUES (?, ?, ?, ?, ?)",
                                            (unidade_id, str(data), tipo, valor, descricao)
                                        )
                                        inseridos += 1
                                    except Exception:
                                        pulados += 1
                                        continue

                                conn.commit()
                                conn.close()

                                msg_pref = " (tabela limpa antes)" if modo_import_despesas.startswith("Sobre") else " (adicionados)"
                                st.success(f"Importação concluída{msg_pref}. Inseridos: {inseridos} | Pulados: {pulados}")
                        except Exception as e:
                            st.error(f"Erro ao processar o arquivo Excel: {e}")
            except Exception as e:
                st.error(f"Erro ao processar o arquivo Excel: {e}")

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

# ============== PRECIFICAÇÃO (TOP-LEVEL) ========================
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
        precos = precos.merge(unidades, left_on="unidade_id", right_on="id", suffixes=("", "_u"))
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




