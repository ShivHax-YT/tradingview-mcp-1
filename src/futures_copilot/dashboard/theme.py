"""Legend-inspired dark theme: layered charcoal panels, micro-labels, green/red
signal accents. Pure CSS — injected once per run."""

GREEN = "#00c805"
RED = "#ff5000"
AMBER = "#ffb224"
BG = "#0c0e12"
PANEL = "#12151c"
PANEL_2 = "#171b23"
BORDER = "#232833"
TEXT = "#e8eaed"
MUTED = "#8b919e"

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

html, body, [class*="css"], .stApp, .stMarkdown, button, input, textarea, select {
  font-family: 'Inter', -apple-system, 'Segoe UI', sans-serif !important;
}
.stApp { background: radial-gradient(1200px 500px at 70% -10%, #131720 0%, #0c0e12 55%) fixed; }
#MainMenu, footer, header[data-testid="stHeader"] { visibility: hidden; height: 0; }
.block-container { padding-top: 1.1rem; padding-bottom: 3rem; max-width: 1500px; }

/* numerals read like a terminal */
.stApp * { font-variant-numeric: tabular-nums; }

/* ── panels ── */
div[data-testid="stVerticalBlockBorderWrapper"] {
  background: linear-gradient(180deg, #141821 0%, #12151c 100%);
  border: 1px solid #232833; border-radius: 12px;
  box-shadow: 0 1px 0 rgba(255,255,255,.02) inset, 0 8px 24px rgba(0,0,0,.35);
}
div[data-testid="stVerticalBlockBorderWrapper"] > div { padding: .35rem .45rem; }

/* ── micro section labels ── */
.microlabel {
  font-size: .68rem; font-weight: 700; letter-spacing: .13em; text-transform: uppercase;
  color: #8b919e; margin: 0 0 .35rem 2px;
}

/* ── header strip ── */
.copilot-brand { font-weight: 800; font-size: 1.05rem; letter-spacing: -.01em; color: #e8eaed; }
.copilot-brand .tick { color: #00c805; }
.bigprice { font-size: 2.1rem; font-weight: 800; letter-spacing: -.02em; line-height: 1; }
.subtle { color: #8b919e; font-size: .8rem; }

/* ── chips ── */
.chip {
  display: inline-flex; align-items: center; gap: .38rem;
  border-radius: 999px; padding: .22rem .7rem; font-size: .72rem; font-weight: 700;
  letter-spacing: .06em; text-transform: uppercase;
  background: #171b23; border: 1px solid #232833; color: #c9cdd6;
}
.chip .dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
.chip.green { color: #46e264; border-color: rgba(0,200,5,.35); background: rgba(0,200,5,.08); }
.chip.green .dot { background: #00c805; box-shadow: 0 0 8px rgba(0,200,5,.8); }
.chip.red { color: #ff7a4d; border-color: rgba(255,80,0,.35); background: rgba(255,80,0,.08); }
.chip.red .dot { background: #ff5000; box-shadow: 0 0 8px rgba(255,80,0,.8); }
.chip.amber { color: #ffc95e; border-color: rgba(255,178,36,.35); background: rgba(255,178,36,.08); }
.chip.amber .dot { background: #ffb224; box-shadow: 0 0 8px rgba(255,178,36,.7); }
.chip.gray .dot { background: #5b6270; }

/* ── decision banner ── */
.decision {
  border-radius: 12px; padding: .9rem 1.1rem; font-weight: 800; font-size: 1.15rem;
  letter-spacing: .04em; display: flex; align-items: center; gap: .7rem;
  border: 1px solid #232833; background: #12151c;
}
.decision.LONG  { border-color: rgba(0,200,5,.45);  background: linear-gradient(180deg, rgba(0,200,5,.12), rgba(0,200,5,.04)); color: #46e264; }
.decision.SHORT { border-color: rgba(255,80,0,.45); background: linear-gradient(180deg, rgba(255,80,0,.12), rgba(255,80,0,.04)); color: #ff7a4d; }
.decision.WAIT  { border-color: rgba(255,178,36,.4); background: linear-gradient(180deg, rgba(255,178,36,.10), rgba(255,178,36,.03)); color: #ffc95e; }
.decision.REJECT{ border-color: #2a2f3a; color: #9aa1ad; }
.decision .sub { font-size: .78rem; font-weight: 600; color: #8b919e; letter-spacing: 0; }

/* ── stat rows ── */
.stat { display: flex; justify-content: space-between; padding: .42rem 2px; border-bottom: 1px solid #1a1e27; }
.stat:last-child { border-bottom: none; }
.stat .k { color: #8b919e; font-size: .8rem; }
.stat .v { font-weight: 600; font-size: .84rem; color: #e8eaed; }
.stat .v.green { color: #46e264; } .stat .v.red { color: #ff7a4d; } .stat .v.muted { color: #5b6270; }

/* ── level ladder ── */
.lvl { display: grid; grid-template-columns: 1fr auto auto; gap: .6rem; padding: .34rem 2px;
       border-bottom: 1px solid #1a1e27; font-size: .84rem; align-items: baseline; }
.lvl:last-child { border-bottom: none; }
.lvl .name { color: #c9cdd6; } .lvl .px { font-weight: 700; }
.lvl .src { color: #5b6270; font-size: .68rem; text-transform: uppercase; letter-spacing: .08em; }

/* ── checklist ── */
.chk { display: flex; gap: .55rem; padding: .3rem 2px; font-size: .82rem; align-items: baseline; }
.chk .mark { font-weight: 800; font-size: .72rem; letter-spacing: .05em; min-width: 2.6rem; }
.chk.pass .mark { color: #46e264; } .chk.fail .mark { color: #ff7a4d; }
.chk .why { color: #8b919e; font-size: .76rem; }

/* ── tabs, Legend style ── */
.stTabs [data-baseweb="tab-list"] { gap: .25rem; background: transparent; border-bottom: 1px solid #232833; }
.stTabs [data-baseweb="tab"] {
  background: transparent; border-radius: 8px 8px 0 0; padding: .45rem .9rem;
  color: #8b919e; font-weight: 600; font-size: .85rem;
}
.stTabs [aria-selected="true"] { color: #e8eaed !important; border-bottom: 2px solid #00c805 !important; }

/* ── inputs/buttons ── */
.stButton button, .stDownloadButton button, .stFormSubmitButton button {
  background: #171b23; color: #e8eaed; border: 1px solid #2a2f3a; border-radius: 9px;
  font-weight: 600; transition: all .15s;
}
.stButton button:hover, .stFormSubmitButton button:hover { border-color: #00c805; color: #46e264; background: rgba(0,200,5,.06); }
div[data-baseweb="select"] > div, .stTextInput input, .stNumberInput input, .stTextArea textarea {
  background: #171b23 !important; border-color: #2a2f3a !important; border-radius: 9px !important;
}

/* dataframes */
div[data-testid="stDataFrame"] { border: 1px solid #232833; border-radius: 10px; }

/* safety footer card */
.safety {
  border: 1px dashed #2a2f3a; border-radius: 10px; padding: .6rem .8rem;
  color: #8b919e; font-size: .74rem; line-height: 1.5;
}
.safety b { color: #c9cdd6; }
</style>
"""
