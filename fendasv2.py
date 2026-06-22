# app_fendas_relatorio.py
# -----------------------------------------------------------------------------
# Abertura de Fendas — Estado II (EN 1992-1-1 §7.3)
# n secções, tabela compacta/toggle, gráfico e PDF detalhado
# + Import/Export CSV (entradas e resultados)
# Requisitos: pip install streamlit plotly reportlab
# -----------------------------------------------------------------------------
import math
import os
from io import BytesIO
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from reportlab.lib.pagesizes import A4
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
)
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ============================== Helpers ==============================

REQUIRED_COLS = ["Secção","b [m]","h [m]","a [m]","As1 [mm²]","As2 [mm²]","ϕ [mm]","M_f [kN·m]"]

def safe(x):
    try:
        return float(x)
    except:
        return float("nan")

def mcr_kNm(fctm, b, h):
    if any(math.isnan(v) or v <= 0 for v in [fctm, b, h]):
        return float("nan")
    # fctm[MPa] → Pa; Mcr em N·m → kN·m
    return (fctm * 1e6 * b * h**2 / 6.0) / 1e3

def _register_unicode_font():
    candidates = [
        "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            pdfmetrics.registerFont(TTFont("UNI", p))
            return "UNI"
    return None

def empty_df(n: int) -> pd.DataFrame:
    return pd.DataFrame({
        "Secção":    [f"S{i+1}" for i in range(n)],
        "b [m]":     [0.0]*n,
        "h [m]":     [0.0]*n,
        "a [m]":     [0.0]*n,
        "As1 [mm²]": [0.0]*n,
        "As2 [mm²]": [0.0]*n,
        "ϕ [mm]":    [0.0]*n,
        "M_f [kN·m]":[0.0]*n,
    })

def normalize_entradas_csv(df_raw: pd.DataFrame) -> pd.DataFrame:
    """Valida/normaliza CSV de ENTRADAS. Mantém apenas REQUIRED_COLS, na ordem certa."""
    cols_lower = {c.lower().strip(): c for c in df_raw.columns}
    # Tentar mapear por equivalências simples
    mapping = {}
    aliases = {
        "secção": "Secção", "secao": "Secção", "secao [id]": "Secção", "secao/id": "Secção",
        "b [m]": "b [m]", "b": "b [m]",
        "h [m]": "h [m]", "h": "h [m]",
        "a [m]": "a [m]", "a": "a [m]",
        "as1 [mm²]": "As1 [mm²]", "as1": "As1 [mm²]",
        "as2 [mm²]": "As2 [mm²]", "as2": "As2 [mm²]",
        "ϕ [mm]": "ϕ [mm]", "phi [mm]": "ϕ [mm]", "diametro [mm]": "ϕ [mm]", "diam [mm]": "ϕ [mm]",
        "m_f [kn·m]": "M_f [kN·m]", "mf [kn·m]": "M_f [kN·m]", "m [kn·m]": "M_f [kN·m]"
    }
    for k_lower, orig in cols_lower.items():
        if k_lower in aliases:
            mapping[orig] = aliases[k_lower]
        elif orig in REQUIRED_COLS:
            mapping[orig] = orig

    df = df_raw.rename(columns=mapping)
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"CSV inválido: faltam colunas obrigatórias {missing}")

    df = df[REQUIRED_COLS].copy()
    # Tipificar numéricos
    num_cols = [c for c in REQUIRED_COLS if c != "Secção"]
    for c in num_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    # 'Secção' como string
    df["Secção"] = df["Secção"].astype(str)
    return df

def df_to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")

# ============================== Config ==============================

st.set_page_config(page_title="Estados Limite de Serviço - Abertura de Fendas", layout="wide")
st.title("💥 Estados Limite de Serviço - Abertura de Fendas")

# ============================== SIDEBAR — PARÂMETROS GERAIS ===================

st.sidebar.title("⚙️ Parâmetros Gerais")

# Secções
st.sidebar.header("📐 Secções de Estudo")
n = st.sidebar.number_input(
    "Número de secções", 1, 200, int(st.session_state.get("n_sec", 3)),
    step=1, help="Define o número de pontos ou secções analisadas."
)

# Materiais
st.sidebar.header("🧱 Materiais")

st.sidebar.subheader("Aço")
Es_GPa = st.sidebar.number_input("Es [GPa]", 150.0, 250.0, 200.0, 5.0)

st.sidebar.subheader("Betão")
Ec_GPa = st.sidebar.number_input("Ec [GPa]", 20.0, 50.0, 30.0, 1.0)
fctm    = st.sidebar.number_input("fctm [MPa]", 0.0, 5.0, 2.9, 0.1)
fct_eff = st.sidebar.number_input("fct,ef [MPa]", 0.0, 5.0, 2.9, 0.1)

# Fluência
st.sidebar.subheader("Fluência")
phi = st.sidebar.number_input("φ — coef. de fluência [-]", 0.0, 3.0, 2.5, 0.1,
                              help="Usado em α=(1+φ)·Es/Ec.")

# Geometria
st.sidebar.header("📏 Propriedades Geométricas")
d2 = st.sidebar.number_input("d₂ [m]", 0.0, 0.3, 0.05, 0.005,
                             help="Distância da face comprimida ao CG da armadura oposta (m).")

# Verificação de abertura de fendas
st.sidebar.header("💥 Verificação de Abertura de Fendas")
k_t_option = st.sidebar.selectbox(
    "kₜ — fator de duração", ["0.6 — Curta duração", "0.4 — Longa duração"]
)
k_t = float(k_t_option.split(" ")[0])

wlim = st.sidebar.number_input("wₖ,lim [mm]", 0.0, 2.0, 0.3, 0.05)

# Derivados (mostra apenas)
alpha   = (1.0 + phi) * (Es_GPa / Ec_GPa) if Ec_GPa > 0 else float("nan")
alpha_e = (Es_GPa / Ec_GPa) if Ec_GPa > 0 else float("nan")
st.sidebar.markdown("---")
st.sidebar.markdown(f"**α = {alpha:.3f}**  **αₑ = {alpha_e:.3f}**")

# ============================== Entradas ==============================

st.subheader("1️⃣ — Dados das Secções")

# Estado inicial de sessão
st.session_state.setdefault("n_sec", 3)
st.session_state.setdefault("df_sec", empty_df(st.session_state["n_sec"]))
st.session_state.setdefault("df_out", None)
st.session_state.setdefault("alpha_val", None)
st.session_state.setdefault("alpha_e_val", None)
st.session_state.setdefault("globais", None)

# Ajustar nº de linhas sem perder dados
desired_n = int(n)
if desired_n != len(st.session_state.df_sec):
    df_tmp = st.session_state.df_sec.copy()
    cur_n = len(df_tmp)
    if desired_n > cur_n:
        for i in range(cur_n, desired_n):
            df_tmp.loc[i] = [f"S{i+1}", 0.0,0.0,0.0,0.0,0.0,0.0,0.0]
    else:
        df_tmp = df_tmp.iloc[:desired_n].reset_index(drop=True)
    st.session_state.df_sec = df_tmp
    st.session_state.n_sec = desired_n

# Editor
col_conf = {
    "Secção": st.column_config.TextColumn("Secção", width="small"),
    "b [m]":  st.column_config.NumberColumn("b [m]", min_value=0.0, step=0.01, format="%.3f"),
    "h [m]":  st.column_config.NumberColumn("h [m]", min_value=0.0, step=0.01, format="%.3f"),
    "a [m]":  st.column_config.NumberColumn("a [m]", min_value=0.0, step=0.005, format="%.3f",
        help="Distância da borda comprimida ao CG da armadura tracionada. d = h − a."),
    "As1 [mm²]": st.column_config.NumberColumn("As1 [mm²]", min_value=0.0, step=25.0, format="%.1f"),
    "As2 [mm²]": st.column_config.NumberColumn("As2 [mm²]", min_value=0.0, step=25.0, format="%.1f"),
    "ϕ [mm]":   st.column_config.NumberColumn("ϕ [mm]", min_value=4.0, step=2.0, format="%.1f",
        help="Diâmetro do varão tracionado."),
    "M_f [kN·m]": st.column_config.NumberColumn("M_f [kN·m]", step=0.1, format="%.3f"),
}

with st.form("form_sec"):
    edited_df = st.data_editor(
        st.session_state.df_sec,
        column_config=col_conf,
        use_container_width=True,
        num_rows="fixed",
        key="grid"
    )
    c1, c2, c3 = st.columns([1,1,1])
    with c1:
        submitted = st.form_submit_button("💾 Guardar alterações", use_container_width=True)
        if submitted:
            st.session_state.df_sec = edited_df.copy()
            st.success("Alterações aplicadas.")
    with c2:
        do_calc = st.form_submit_button("▶️ Calcular", use_container_width=True)
    with c3:
        pass  # reservado

# ============================== Import/Export CSV (ENTRADAS/RESULTADOS) ======

st.markdown("### 1.1 — Importar / Exportar CSV")

cimp, cexp, cexp2 = st.columns([1,1,1])

with cimp:
    up = st.file_uploader("Importar ENTRADAS (.csv)", type=["csv"], accept_multiple_files=False)
    if up is not None:
        try:
            df_raw = pd.read_csv(up)
        except Exception:
            # tenta ; como separador
            up.seek(0)
            df_raw = pd.read_csv(up, sep=";")
        try:
            df_norm = normalize_entradas_csv(df_raw)
            st.session_state.df_sec = df_norm.copy()
            st.session_state.n_sec = len(df_norm)
            st.success(f"Importado com sucesso: {len(df_norm)} linhas.")
        except Exception as e:
            st.error(str(e))

with cexp:
    csv_in = df_to_csv_bytes(st.session_state.df_sec)
    st.download_button(
        "⬇️ Exportar ENTRADAS (CSV)",
        data=csv_in,
        file_name="entradas_fendas.csv",
        mime="text/csv",
        use_container_width=True
    )

with cexp2:
    if st.session_state.get("df_out") is not None:
        csv_out = df_to_csv_bytes(st.session_state.df_out)
        st.download_button(
            "⬇️ Exportar RESULTADOS (CSV)",
            data=csv_out,
            file_name="resultados_fendas.csv",
            mime="text/csv",
            use_container_width=True
        )
    else:
        st.button("⬇️ Exportar RESULTADOS (CSV)", disabled=True, use_container_width=True)

# (Opcional) Guardar localmente — útil quando corres localmente
with st.expander("💽 Guardar localmente (opcional)"):
    os.makedirs("data", exist_ok=True)
    col_local1, col_local2 = st.columns(2)
    with col_local1:
        fname_in = st.text_input("Nome ficheiro ENTRADAS", value="data/entradas_fendas.csv")
        if st.button("Guardar ENTRADAS no disco"):
            try:
                st.session_state.df_sec.to_csv(fname_in, index=False)
                st.success(f"Guardado em {fname_in}")
            except Exception as e:
                st.error(f"Falha a guardar: {e}")
    with col_local2:
        fname_out = st.text_input("Nome ficheiro RESULTADOS", value="data/resultados_fendas.csv")
        if st.button("Guardar RESULTADOS no disco", disabled=st.session_state.get("df_out") is None):
            try:
                st.session_state.df_out.to_csv(fname_out, index=False)
                st.success(f"Guardado em {fname_out}")
            except Exception as e:
                st.error(f"Falha a guardar: {e}")

# ============================== Cálculo ==============================

def calcular(df):
    Es = Es_GPa * 1e9
    Ec = Ec_GPa * 1e9
    alpha_local   = (1.0 + phi) * (Es / Ec) if Es > 0 and Ec > 0 else float("nan")
    alpha_e_local = (Es_GPa / Ec_GPa) if Ec_GPa > 0 else float("nan")

    resultados = []
    for _, r in df.iterrows():
        b = safe(r["b [m]"]); h = safe(r["h [m]"]); a = safe(r["a [m]"])
        As1_mm2 = safe(r["As1 [mm²]"]); As2_mm2 = safe(r["As2 [mm²]"])
        phi_mm  = safe(r["ϕ [mm]"]);     M_kNm  = safe(r["M_f [kN·m]"])

        # Geometria/áreas
        d = h - a if (h > 0 and a >= 0 and h > a) else float("nan")
        As1 = As1_mm2 * 1e-6 if As1_mm2 > 0 else float("nan")
        As2 = As2_mm2 * 1e-6 if As2_mm2 > 0 else float("nan")
        rho1 = As1 / (b * d) if (b > 0 and d > 0 and not math.isnan(As1)) else float("nan")
        rho2 = As2 / (b * d) if (b > 0 and d > 0 and not math.isnan(As2)) else float("nan")
        beta = (As1_mm2 / As2_mm2) if (As1_mm2 > 0 and As2_mm2 > 0) else float("nan")

        # 1) Estado I — Mcr
        Mcr = mcr_kNm(fctm, b, h)

        # >>> Secção não fendilhada (|M_f| < M_cr)
        if (not math.isnan(M_kNm)) and (not math.isnan(Mcr)) and (abs(M_kNm) < Mcr):
            resultados.append({
                "Secção": r["Secção"], "b [m]": b, "h [m]": h, "a [m]": a, "d [m]": d,
                "As1 [mm²]": As1_mm2, "As2 [mm²]": As2_mm2, "ϕ [mm]": phi_mm, "M_f [kN·m]": M_kNm,
                "ρ1 [-]": float("nan"), "ρ2 [-]": float("nan"), "β [-]": float("nan"),
                "Mcr [kN·m]": Mcr, "k^II [-]": float("nan"), "x [m]": float("nan"), "I^II [m^4]": float("nan"),
                "σs1 [MPa]": float("nan"),
                "h_c,ef [m]": float("nan"), "A_c,ef [m²]": float("nan"), "ρ_eff [-]": float("nan"), "s_r,max [m]": float("nan"),
                "ε_sm−ε_cm [-]": float("nan"), "w_k [mm]": float("nan"),
                "Conformidade": "🟢 Secção não fendilhada"
            })
            continue

        # 2) Linha neutra (Estado II) — k, x
        if any(math.isnan(v) for v in [alpha_local, rho1, d]) or rho1 <= 0:
            k = float("nan")
        else:
            beta_eff = 0.0 if math.isnan(beta) else float(beta)
            A = alpha_local * rho1 * (1.0 + beta_eff)
            B = 2.0 * alpha_local * rho1 * (1.0 + beta_eff * (d2 / d)) if d > 0 else float("nan")
            disc = A*A + (B if not math.isnan(B) else 0.0)
            k = -A + math.sqrt(disc) if (not math.isnan(B) and disc >= 0) else float("nan")
        x = k * d if (not math.isnan(k) and not math.isnan(d)) else float("nan")

        # 2) Inércia fendilhada I^II
        if any(math.isnan(v) for v in [b, d, k, rho1]) or b <= 0 or d <= 0:
            I2 = float("nan")
        else:
            beta_eff = 0.0 if math.isnan(beta) else float(beta)
            I2 = b * (d ** 3) * (
                (k ** 3) / 3.0
                + alpha_local * rho1 * ((1.0 - k) ** 2 + beta_eff * (k - d2 / d) ** 2)
            )

        # 3) Tensões (MPa)
        M_Nm = M_kNm * 1e3 if not math.isnan(M_kNm) else float("nan")
        sig_s1 = (alpha_local * M_Nm * (d - k * d) / I2) / 1e6 if (not math.isnan(I2) and I2 > 0) else float("nan")

        # 4) hc,ef, Ac,ef, rho_eff, s_r,max
        hc_candidates = []
        if (h > 0 and d > 0) and not math.isnan(x):
            t1 = 2.5 * (h - d)
            t2 = (h - x) / 3.0 if x < h else h / 3.0
            t3 = h / 2.0
            for t in [t1, t2, t3]:
                if t > 0:
                    hc_candidates.append(t)
        hc_ef = min(hc_candidates) if hc_candidates else float("nan")
        Acef = b * hc_ef if (not math.isnan(hc_ef) and hc_ef > 0 and b > 0) else float("nan")
        rho_eff = (As1 / Acef) if (not math.isnan(Acef) and Acef > 0 and not math.isnan(As1)) else float("nan")
        phi_m = phi_mm * 1e-3 if phi_mm > 0 else float("nan")

        sr_max = float("nan")
        if (not math.isnan(rho_eff)) and rho_eff > 0 and (not math.isnan(phi_m)) and phi_m > 0:
            sr_max = 3.4 * 0.03 + 0.425 * 0.8 * 0.5 * (phi_m / rho_eff)

        # 5) ε_sm − ε_cm
        Es_MPa = Es_GPa * 1000.0
        eps_diff = float("nan")
        if (not math.isnan(sig_s1)) and (not math.isnan(rho_eff)) and rho_eff > 0 and Es_MPa > 0:
            eps_diff = (sig_s1 / Es_MPa) - k_t * (fct_eff / (Es_MPa * rho_eff)) * (1.0 + (Es_GPa / Ec_GPa) * rho_eff)

        # 6) w_k
        wk_m  = sr_max * eps_diff if (not math.isnan(sr_max) and not math.isnan(eps_diff) and sr_max > 0) else float("nan")
        wk_mm = wk_m * 1000.0 if not math.isnan(wk_m) else float("nan")
        conf  = ("✅ Conforme" if (not math.isnan(wk_mm) and wk_mm <= wlim) else
                 ("❌ Não conforme" if not math.isnan(wk_mm) else "—"))

        resultados.append({
            "Secção": r["Secção"], "b [m]": b, "h [m]": h, "a [m]": a, "d [m]": d,
            "As1 [mm²]": As1_mm2, "As2 [mm²]": As2_mm2, "ϕ [mm]": phi_mm, "M_f [kN·m]": M_kNm,
            "ρ1 [-]": rho1, "ρ2 [-]": rho2, "β [-]": beta,
            "Mcr [kN·m]": Mcr, "k^II [-]": k, "x [m]": x, "I^II [m^4]": I2,
            "σs1 [MPa]": sig_s1,
            "h_c,ef [m]": hc_ef, "A_c,ef [m²]": Acef, "ρ_eff [-]": rho_eff, "s_r,max [m]": sr_max,
            "ε_sm−ε_cm [-]": eps_diff, "w_k [mm]": wk_mm, "Conformidade": conf
        })

    return pd.DataFrame(resultados), alpha_local, alpha_e_local

# Executa cálculo quando pedido e GUARDA em sessão
if 'do_calc_flag' not in st.session_state:
    st.session_state.do_calc_flag = False
if do_calc:
    st.session_state.df_sec = edited_df.copy()
    df_out, alpha_val, alpha_e_val = calcular(st.session_state.df_sec.copy())
    st.session_state.df_out = df_out
    st.session_state.alpha_val = alpha_val
    st.session_state.alpha_e_val = alpha_e_val
    st.session_state.globais = {
        "Es_GPa": Es_GPa, "Ec_GPa": Ec_GPa, "phi": phi,
        "alpha": alpha_val, "alpha_e": alpha_e_val, "fctm": fctm,
        "fct_eff": fct_eff, "d2": d2, "k_t": k_t, "wlim": wlim
    }
    st.session_state.do_calc_flag = True

# Se ainda não há resultados, informa e sai
if st.session_state.df_out is None:
    st.info("⚠️ Introduz os dados e carrega em **Calcular** para ver resultados, gerar PDF e exportar RESULTADOS.")
    st.stop()

# Usa os resultados guardados
df_out = st.session_state.df_out.copy()
alpha_val = st.session_state.alpha_val
alpha_e_val = st.session_state.alpha_e_val

# ============================== Resultados ===============================

st.subheader("2️⃣ — Resultados e Verificação")

compact_cols = [
    "Secção","b [m]","h [m]","d [m]","As1 [mm²]","As2 [mm²]","M_f [kN·m]",
    "s_r,max [m]","ε_sm−ε_cm [-]","w_k [mm]","Conformidade"
]
full_cols = [
    "Secção","b [m]","h [m]","a [m]","d [m]","As1 [mm²]","As2 [mm²]","ϕ [mm]","M_f [kN·m]",
    "ρ1 [-]","ρ2 [-]","β [-]","Mcr [kN·m]","k^II [-]","x [m]","I^II [m^4]","σs1 [MPa]",
    "h_c,ef [m]","A_c,ef [m²]","ρ_eff [-]","s_r,max [m]","ε_sm−ε_cm [-]","w_k [mm]","Conformidade"
]

show_all = st.toggle("Mostrar todas as colunas de cálculo", value=False)
cols_to_show = full_cols if show_all else compact_cols
df_show = df_out[cols_to_show].copy()

def color_wk(v):
    if isinstance(v, float) and math.isnan(v):
        return ""
    return "background-color:#f44336;color:white;" if v > wlim else "background-color:#4CAF50;color:white;"

sty = df_show.style
try:
    sty = sty.map(color_wk, subset=["w_k [mm]"])  # pandas ≥ 2.3
except Exception:
    sty = sty.applymap(color_wk, subset=["w_k [mm]"])  # fallback pandas antigos
st.dataframe(sty, use_container_width=True)

# Contagem de conformidade (inclui “Secção não fendilhada” como conforme)
nf_mask = df_out["Conformidade"].str.contains("não fendilhada", case=False, na=False)
wk_mask = (df_out["w_k [mm]"] <= wlim) & (~df_out["w_k [mm]"].isna())
ok = int(nf_mask.sum() + wk_mask.sum())
nok = int(len(df_out) - ok)
st.success(f"✅ Secções conformes: {ok}/{len(df_out)} | ❌ Não conformes: {nok}")

# ============================== Gráfico =======================================

def grafico_wk(df, wlim):
    dfp = df[["Secção","w_k [mm]","Conformidade"]].copy()
    dfp["w_plot"] = dfp["w_k [mm]"].fillna(0.0)  # NF como 0 para visualizar
    cores = []
    for v, conf in zip(dfp["w_plot"], dfp["Conformidade"]):
        if isinstance(conf, str) and "não fendilhada" in conf.lower():
            cores.append("#4CAF50")
        else:
            cores.append("#4CAF50" if v <= wlim else "#F44336")

    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=dfp["Secção"], x=dfp["w_plot"], orientation='h',
        marker_color=cores,
        text=[("NF" if (isinstance(c, str) and "não fendilhada" in c.lower()) else f"{v:.3f}")
              for v, c in zip(dfp["w_plot"], dfp["Conformidade"])],
        textposition='auto'
    ))
    fig.add_vline(x=wlim, line_dash="dash", line_color="black",
                  annotation_text=f"Limite = {wlim:.3f} mm", annotation_position="top right")
    fig.update_layout(title="📊 Abertura de fendas por secção",
                      xaxis_title="wₖ [mm] (NF=Secção não fendilhada)",
                      yaxis_title="Secção",
                      height=400 + 25*len(dfp), template="simple_white")
    return fig

if st.toggle("Mostrar gráfico de verificação", True):
    st.plotly_chart(grafico_wk(df_out, wlim), use_container_width=True)

# ============================== PDF DETALHADO ================================

def gerar_relatorio_pdf(df, globais):
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    font_name = _register_unicode_font()
    if font_name:
        for k in ["Title","Heading1","Heading2","Heading3","Normal"]:
            styles[k].fontName = font_name
    N  = styles["Normal"]; H = styles["Heading2"]; HT = styles["Heading3"]

    elems = []
    elems.append(Paragraph("<b>Abertura de Fendas — Relatório de Cálculo</b>", styles["Title"]))
    elems.append(Paragraph(f"Gerado em: {date.today().strftime('%d/%m/%Y')}", N))
    elems.append(Spacer(1, 8))
    elems.append(Paragraph(
        f"Parâmetros globais: Es={globais['Es_GPa']:.1f} GPa, Ec={globais['Ec_GPa']:.1f} GPa, "
        f"φ={globais['phi']:.2f}, α={(globais['alpha']):.3f}, α<sub>e</sub>={(globais['alpha_e']):.3f}, "
        f"fctm={globais['fctm']:.2f} MPa, fct,ef={globais['fct_eff']:.2f} MPa, "
        f"d<sub>2</sub>={globais['d2']:.3f} m, k<sub>t</sub>={globais['k_t']:.2f}, "
        f"w<sub>k,lim</sub>={globais['wlim']:.3f} mm",
        N
    ))
    elems.append(Spacer(1, 10))

    for _, r in df.iterrows():
        elems.append(Paragraph(f"<b>Secção {r['Secção']}</b>", H))
        elems.append(Spacer(1, 4))

        # 1. Estado I — Mcr
        elems.append(Paragraph("1. Estado I — Momento de fendilhação", HT))
        elems.append(Paragraph(
            f"M<sub>cr</sub> = f<sub>ctm</sub> · b · h<super>2</super> / 6 = "
            f"{globais['fctm']:.3f} · {r['b [m]']:.3f} · ({r['h [m]']:.3f})<super>2</super> / 6 "
            f"= {r['Mcr [kN·m]']:.3f} kN·m", N))

        # 2. Linha neutra / I'' (Estado II)
        if isinstance(r["Conformidade"], str) and "não fendilhada" in r["Conformidade"].lower():
            elems.append(Paragraph(
                "A secção não atinge o momento de fendilhação (|M<sub>f</sub>| < M<sub>cr</sub>). "
                "Cálculos do Estado II não se aplicam.", N))
        else:
            elems.append(Paragraph("2. Cálculo da posição da linha neutra (Estado II)", HT))
            elems.append(Paragraph(
                f"ρ<sub>1</sub> = A<sub>s1</sub>/(b·d) = {r['ρ1 [-]']:.5f} ; "
                f"ρ<sub>2</sub> = A<sub>s2</sub>/(b·d) = {(0.0 if math.isnan(r['ρ2 [-]']) else r['ρ2 [-]']):.5f} ; "
                f"β = A<sub>s1</sub>/A<sub>s2</sub> = {(0.0 if math.isnan(r['β [-]']) else r['β [-]']):.3f}", N))
            elems.append(Paragraph(
                f"k<sup>II</sup> = {(0.0 if math.isnan(r['k^II [-]']) else r['k^II [-]']):.5f} ; "
                f"x = k·d = {(0.0 if math.isnan(r['x [m]']) else r['x [m]']):.3f} m", N))
            elems.append(Paragraph(
                "I<sup>II</sup> = b·d<super>3</super>[ k<super>3</super>/3 + α·ρ<sub>1</sub>((1−k)<super>2</super> "
                f"+ β·(k−d<sub>2</sub>/d)<super>2</super>) ] = "
                f"{(0.0 if math.isnan(r['I^II [m^4]']) else r['I^II [m^4]']):.6f} m<super>4</super>", N))

            # 3. Tensões
            elems.append(Paragraph("3. Tensões na armadura", HT))
            elems.append(Paragraph(
                f"σ<sub>s1</sub> = α·M·(d−k·d)/I<sup>II</sup> = "
                f"{(0.0 if math.isnan(r['σs1 [MPa]']) else r['σs1 [MPa]']):.2f} MPa", N))

            # 4. Distância máxima entre fendas
            elems.append(Paragraph("4. Cálculo da distância máxima entre fendas", HT))
            elems.append(Paragraph(
                f"h<sub>c,ef</sub> = min{{2.5(h−d); (h−x)/3; h/2}} = "
                f"{(0.0 if math.isnan(r['h_c,ef [m]']) else r['h_c,ef [m]']):.3f} m", N))
            elems.append(Paragraph(
                f"A<sub>c,ef</sub> = b·h<sub>c,ef</sub> = "
                f"{(0.0 if math.isnan(r['A_c,ef [m²]']) else r['A_c,ef [m²]']):.4f} m<super>2</super>", N))
            elems.append(Paragraph(
                f"ρ<sub>eff</sub> = A<sub>s1</sub>/A<sub>c,ef</sub> = "
                f"{(0.0 if math.isnan(r['ρ_eff [-]']) else r['ρ_eff [-]']):.5f}", N))
            elems.append(Paragraph(
                "s<sub>r,max</sub> = 3.4·0.03 + 0.425·0.8·0.5·(ϕ/ρ<sub>eff</sub>) = "
                f"{(0.0 if math.isnan(r['s_r,max [m]']) else r['s_r,max [m]']):.4f} m", N))

            # 5. Extensão média
            elems.append(Paragraph("5. Extensão média entre o aço e o betão", HT))
            elems.append(Paragraph(
                "ε<sub>sm</sub> − ε<sub>cm</sub> = σ<sub>s1</sub>/E<sub>s</sub> − "
                "k<sub>t</sub>·f<sub>ct,ef</sub>/(E<sub>s</sub>·ρ<sub>eff</sub>)·(1+α<sub>e</sub>·ρ<sub>eff</sub>) "
                f"= {(0.0 if math.isnan(r['ε_sm−ε_cm [-]']) else r['ε_sm−ε_cm [-]']):.6f}", N))

            # 6. w_k
            elems.append(Paragraph("6. Valor característico da abertura de fendas", HT))
            elems.append(Paragraph(
                "w<sub>k</sub> = s<sub>r,max</sub> · (ε<sub>sm</sub> − ε<sub>cm</sub>) = "
                f"{(0.0 if math.isnan(r['w_k [mm]']) else r['w_k [mm]']):.3f} mm", N))

        # Conclusão
        elems.append(Spacer(1, 6))
        conf = r["Conformidade"]
        is_green = ("conforme" in str(conf).lower()) or ("não fendilhada" in str(conf).lower())
        cor = colors.green if is_green else colors.red
        tbl_conf = Table([["Resultado:", conf]])
        tbl_conf.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(0,0),colors.lightgrey),
            ("TEXTCOLOR",(1,0),(1,0),cor),
            ("GRID",(0,0),(-1,-1),0.25,colors.grey),
            ("ALIGN",(0,0),(-1,-1),"LEFT")
        ]))
        elems.append(tbl_conf)
        elems.append(Spacer(1, 12))
        elems.append(PageBreak())

    doc.build(elems)
    buf.seek(0)
    return buf

# Globais atuais para o PDF
globais_at_calc = st.session_state.globais or {
    "Es_GPa": Es_GPa, "Ec_GPa": Ec_GPa, "phi": phi,
    "alpha": st.session_state.alpha_val, "alpha_e": st.session_state.alpha_e_val, "fctm": fctm,
    "fct_eff": fct_eff, "d2": d2, "k_t": k_t, "wlim": wlim
}

st.markdown("---")
col_pdf1, col_pdf2 = st.columns([1,3])
with col_pdf1:
    if st.button("📄 Gerar Relatório Técnico (PDF)", use_container_width=True):
        pdf = gerar_relatorio_pdf(df_out, globais_at_calc)
        st.download_button("⬇️ Download PDF", data=pdf, file_name="relatorio_fendas.pdf",
                           mime="application/pdf", use_container_width=True)
with col_pdf2:
    st.caption("O PDF usa os resultados da sessão.")

# ==============================================================================
st.caption("Cálculo de abertura de fendas pelo Eurocódigo 2 — inclui import/export CSV (entradas e resultados)")
