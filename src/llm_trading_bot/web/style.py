"""Stylesheet of the web UI: a dark trading-terminal look built from CSS and inline SVG only.

Everything is served from ``/app.css`` because the CSP allows neither inline styles nor external
fonts or images; motion stops for visitors who ask for reduced motion.
"""

CSS = """
:root {
  color-scheme: dark;
  --void: #030303; --deep: #0b0b0c; --panel: rgba(12, 12, 14, 0.86); --well: rgba(0, 0, 0, 0.72);
  --line: #222326; --line-hot: #3a3c42; --grid: rgba(255, 255, 255, 0.032);
  --text: #e8e9ec; --muted: #94979e; --dim: #5d6067;
  --cyan: #2de2e6; --blue: #12a5a9; --magenta: #ff3d8b; --amber: #ffb547; --lime: #6bff9a;
  --bad: #ff5c7a;
  --mono: "JetBrains Mono", "Cascadia Code", "Fira Code", Consolas, "SF Mono", monospace;
  --sans: "Segoe UI Variable Text", "Segoe UI", system-ui, -apple-system, sans-serif;
}
* { box-sizing: border-box; }
html { background: var(--void); }
body {
  margin: 0; min-height: 100vh; font: 15px/1.55 var(--sans); color: var(--text);
  background:
    radial-gradient(1100px 620px at 88% -12%, rgba(255, 61, 139, 0.08), transparent 62%),
    radial-gradient(900px 640px at -12% 112%, rgba(45, 226, 230, 0.06), transparent 60%),
    linear-gradient(var(--grid) 1px, transparent 1px) 0 0 / 44px 44px,
    linear-gradient(90deg, var(--grid) 1px, transparent 1px) 0 0 / 44px 44px,
    var(--void);
  background-attachment: fixed;
  display: flex; flex-direction: column;
}
body::after {
  content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 40;
  background: repeating-linear-gradient(transparent 0 2px, rgba(255, 255, 255, 0.018) 2px 3px);
}
::selection { background: rgba(45, 226, 230, 0.35); color: #fff; }

/* ---------------------------------------------------------------- backdrop: circuit + chart */
.backdrop { position: fixed; inset: 0; width: 100%; height: 100%; z-index: 0;
  pointer-events: none; opacity: 0.9; }
.trace { fill: none; stroke: rgba(45, 226, 230, 0.16); stroke-width: 1.4; }
.trace.warm { stroke: rgba(255, 61, 139, 0.14); }
.pulse { fill: none; stroke: var(--cyan); stroke-width: 2; stroke-linecap: round;
  stroke-dasharray: 10 620; animation: flow 7s linear infinite; opacity: 0.75; }
.pulse.warm { stroke: var(--magenta); animation-duration: 9s; animation-delay: -3s; }
.pulse.slow { animation-duration: 12s; animation-delay: -6s; }
.node { fill: var(--void); stroke: rgba(45, 226, 230, 0.45); stroke-width: 1.4; }
.node.hot { fill: var(--cyan); stroke: none; animation: blink 2.8s ease-in-out infinite; }
.node.warm { stroke: rgba(255, 61, 139, 0.5); }
.chip-pad { fill: rgba(10, 10, 11, 0.92); stroke: rgba(45, 226, 230, 0.28); stroke-width: 1.2; }
.chip-pin { stroke: rgba(45, 226, 230, 0.28); stroke-width: 1.2; }
.candle-up { fill: rgba(45, 226, 230, 0.2); stroke: rgba(45, 226, 230, 0.4); }
.candle-down { fill: rgba(255, 61, 139, 0.16); stroke: rgba(255, 61, 139, 0.38); }
.wick-up { stroke: rgba(45, 226, 230, 0.4); }
.wick-down { stroke: rgba(255, 61, 139, 0.38); }
.price { fill: none; stroke: rgba(255, 181, 71, 0.55); stroke-width: 1.6;
  stroke-dasharray: 1400; stroke-dashoffset: 1400; animation: draw 5s ease-out 0.4s forwards; }
.axis { stroke: rgba(150, 152, 158, 0.14); stroke-dasharray: 2 6; }
@keyframes flow { to { stroke-dashoffset: -630; } }
@keyframes draw { to { stroke-dashoffset: 0; } }
@keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0.25; } }
@keyframes spin { to { transform: rotate(360deg); } }
@keyframes ticker { to { transform: translateX(-50%); } }
@keyframes caret { 50% { opacity: 0; } }
/* transform only: a paused animation (background tab) must never hide content */
@keyframes rise { from { transform: translateY(10px); } }
@keyframes sheen { from { transform: translateX(-120%) skewX(-20deg); }
  to { transform: translateX(260%) skewX(-20deg); } }
@keyframes ping { 0% { r: 3; opacity: 1; } 100% { r: 14; opacity: 0; } }

/* ---------------------------------------------------------------- header + ticker */
header, .ticker, main, footer { position: relative; z-index: 1; }
header {
  display: flex; flex-wrap: wrap; gap: 12px 20px; justify-content: space-between;
  align-items: center; padding: 12px 22px;
  background: linear-gradient(180deg, rgba(10, 10, 11, 0.96), rgba(3, 3, 3, 0.88));
  border-bottom: 1px solid var(--line); backdrop-filter: blur(8px);
}
header::after { content: ""; position: absolute; left: 0; right: 0; bottom: -1px; height: 1px;
  background: linear-gradient(90deg, transparent, var(--cyan) 30%, var(--magenta) 70%,
    transparent); opacity: 0.7; }
.brand { display: flex; align-items: center; gap: 12px; }
.brand svg { width: 38px; height: 38px; flex: none; }
.logo-hex { fill: rgba(45, 226, 230, 0.06); stroke: var(--cyan); stroke-width: 1.6; }
.logo-ring { fill: none; stroke: rgba(255, 61, 139, 0.55); stroke-width: 1;
  stroke-dasharray: 3 5; transform-box: view-box; transform-origin: 50% 50%;
  animation: spin 18s linear infinite; }
.logo-up { fill: var(--cyan); }
.logo-down { fill: var(--magenta); }
.logo-wick { stroke: var(--text); stroke-width: 1.2; opacity: 0.7; }
.brand-name { font: 700 15px/1.1 var(--mono); letter-spacing: 0.16em; text-transform: uppercase;
  margin: 0; }
.brand-name span { color: var(--cyan); }
.brand-sub { font: 11px/1.4 var(--mono); color: var(--muted); letter-spacing: 0.12em;
  text-transform: uppercase; margin-top: 3px; }
.status { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.status form { margin: 0; }
.chip { display: inline-flex; align-items: center; gap: 7px; padding: 4px 10px;
  border: 1px solid var(--line-hot); border-radius: 2px; background: rgba(0, 0, 0, 0.6);
  font: 600 11px/1.4 var(--mono); letter-spacing: 0.12em; text-transform: uppercase;
  color: var(--muted); }
.chip.ok { color: var(--lime); border-color: rgba(107, 255, 154, 0.35); }
.chip.lock { color: var(--amber); border-color: rgba(255, 181, 71, 0.35); }
.led { width: 7px; height: 7px; border-radius: 50%; background: currentColor;
  box-shadow: 0 0 6px currentColor, 0 0 12px currentColor; animation: blink 2.2s infinite; }
.chip.lock .led { animation: none; border-radius: 1px; }
.ticker { overflow: hidden; border-bottom: 1px solid var(--line);
  background: rgba(0, 0, 0, 0.82); font: 11px/1 var(--mono); letter-spacing: 0.14em;
  text-transform: uppercase; color: var(--dim); }
.ticker-track { display: inline-flex; white-space: nowrap; animation: ticker 48s linear infinite; }
.ticker-track span { padding: 9px 22px; border-right: 1px solid var(--line); }
.ticker-track b { color: var(--cyan); font-weight: 600; margin-right: 8px; }
.ticker-track span:nth-child(3n) b { color: var(--magenta); }
.ticker-track span:nth-child(3n+2) b { color: var(--amber); }

/* ---------------------------------------------------------------- layout */
main { width: 100%; max-width: 1040px; margin: 30px auto 40px; padding: 0 16px; flex: 1; }
footer { padding: 14px 22px; border-top: 1px solid var(--line); background: rgba(0, 0, 0, 0.8);
  font: 11px/1.6 var(--mono); letter-spacing: 0.12em; text-transform: uppercase;
  color: var(--dim); display: flex; flex-wrap: wrap; gap: 6px 22px; justify-content: center; }
footer span::before { content: "\\25C6  "; color: var(--line-hot); }
.grid { display: grid; grid-template-columns: repeat(12, 1fr); gap: 20px; }
.span-7 { grid-column: span 7; } .span-5 { grid-column: span 5; }
.span-12 { grid-column: span 12; }
@media (max-width: 860px) { .span-7, .span-5 { grid-column: span 12; } }

/* ---------------------------------------------------------------- panels */
.panel {
  position: relative; background: var(--panel); border: 1px solid var(--line);
  border-radius: 3px; padding: 20px 22px 22px; margin-bottom: 20px;
  box-shadow: 0 24px 60px -28px rgba(0, 0, 0, 0.9), inset 0 1px 0 rgba(255, 255, 255, 0.03);
  backdrop-filter: blur(6px); animation: rise 0.5s ease-out both;
}
.grid .panel { margin-bottom: 0; }
.panel:nth-child(2) { animation-delay: 0.08s; } .panel:nth-child(3) { animation-delay: 0.16s; }
.panel::before {
  content: ""; position: absolute; inset: -1px; pointer-events: none;
  background:
    linear-gradient(var(--cyan), var(--cyan)) left top / 20px 2px,
    linear-gradient(var(--cyan), var(--cyan)) left top / 2px 20px,
    linear-gradient(var(--cyan), var(--cyan)) right top / 20px 2px,
    linear-gradient(var(--cyan), var(--cyan)) right top / 2px 20px,
    linear-gradient(var(--magenta), var(--magenta)) left bottom / 20px 2px,
    linear-gradient(var(--magenta), var(--magenta)) left bottom / 2px 20px,
    linear-gradient(var(--magenta), var(--magenta)) right bottom / 20px 2px,
    linear-gradient(var(--magenta), var(--magenta)) right bottom / 2px 20px;
  background-repeat: no-repeat;
}
.panel-head { display: flex; align-items: center; gap: 12px; margin: 0 0 16px;
  padding-bottom: 12px; border-bottom: 1px dashed var(--line); }
.panel-head .tag { font: 600 10px/1 var(--mono); letter-spacing: 0.18em; color: var(--void);
  text-transform: uppercase;
  background: var(--cyan); padding: 4px 6px; border-radius: 1px; }
.panel-head .tag.warm { background: var(--magenta); }
.panel-head .tag.amber { background: var(--amber); }
h2 { font: 700 14px/1.2 var(--mono); letter-spacing: 0.14em; text-transform: uppercase;
  margin: 0; flex: 1; }
.leds { display: flex; gap: 5px; }
.leds i { width: 6px; height: 6px; border-radius: 50%; background: var(--dim); }
.leds i:first-child { background: var(--lime); box-shadow: 0 0 8px var(--lime); }
.leds i:nth-child(2) { background: var(--amber); animation: blink 1.6s infinite; }
p { margin: 0 0 10px; }
.hint { color: var(--muted); font-size: 13px; margin: 8px 0 0; }
code { font: 12.5px var(--mono); color: var(--cyan); background: rgba(45, 226, 230, 0.08);
  padding: 1px 5px; border-radius: 2px; }
strong { color: #fff; }

/* ---------------------------------------------------------------- readouts */
.readouts { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px;
  margin-bottom: 20px; }
.readout { position: relative; padding: 12px 14px 12px 16px; background: var(--well);
  border: 1px solid var(--line); border-radius: 2px; overflow: hidden;
  animation: rise 0.45s ease-out both; }
.readout::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
  background: var(--cyan); box-shadow: 0 0 12px var(--cyan); }
.readout:nth-child(2)::before { background: var(--magenta); box-shadow: 0 0 12px var(--magenta); }
.readout:nth-child(3)::before { background: var(--amber); box-shadow: 0 0 12px var(--amber); }
.readout:nth-child(4)::before { background: var(--lime); box-shadow: 0 0 12px var(--lime); }
.readout small { display: block; font: 10px/1.2 var(--mono); letter-spacing: 0.18em;
  text-transform: uppercase; color: var(--dim); }
.readout b { display: block; margin-top: 6px; font: 600 17px/1.2 var(--mono); color: var(--text);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.readout b.off { color: var(--amber); }

/* ---------------------------------------------------------------- forms */
label { display: block; margin: 16px 0 6px; font: 600 11px/1.3 var(--mono);
  letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted); }
label::before { content: "\\203A  "; color: var(--cyan); }
input[type=text], input[type=password], input[type=number], select {
  width: 100%; padding: 11px 12px; font: 14px/1.3 var(--mono); color: var(--text);
  background: var(--well); border: 1px solid var(--line); border-radius: 2px;
  caret-color: var(--cyan); transition: border-color 0.15s, box-shadow 0.15s;
}
input::placeholder { color: var(--dim); }
input:hover, select:hover { border-color: var(--line-hot); }
input:focus, select:focus { outline: none; border-color: var(--cyan);
  box-shadow: 0 0 0 3px rgba(45, 226, 230, 0.14), 0 0 22px rgba(45, 226, 230, 0.22); }
select { appearance: none; padding-right: 34px; cursor: pointer;
  background: linear-gradient(45deg, transparent 50%, var(--cyan) 50%) right 18px center / 6px 6px
      no-repeat,
    linear-gradient(135deg, var(--cyan) 50%, transparent 50%) right 12px center / 6px 6px
      no-repeat,
    var(--well); }
.fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 0 14px; }
.check { display: flex; gap: 10px; align-items: center; margin: 14px 0 0; cursor: pointer;
  font: 13px/1.4 var(--sans); letter-spacing: 0; text-transform: none; color: var(--muted); }
.check::before { content: none; }
input[type=checkbox] { appearance: none; flex: none; width: 18px; height: 18px; margin: 0;
  border: 1.5px solid var(--line-hot); border-radius: 2px; background: var(--well);
  cursor: pointer; transition: all 0.15s; }
input[type=checkbox]:checked { border-color: var(--cyan); background: var(--cyan);
  box-shadow: inset 0 0 0 3px var(--void), 0 0 12px rgba(45, 226, 230, 0.6); }
.check input[type=checkbox]:checked { border-color: var(--bad); background: var(--bad);
  box-shadow: inset 0 0 0 3px var(--void), 0 0 12px rgba(255, 92, 122, 0.6); }
input[type=checkbox]:focus-visible { outline: 2px solid var(--cyan); outline-offset: 2px; }
.keystate { display: flex; align-items: center; gap: 10px; padding: 10px 12px;
  background: var(--well); border: 1px dashed var(--line-hot); border-radius: 2px;
  font: 13px/1.4 var(--mono); }
.keystate .led { color: var(--lime); }
.keystate.off .led { color: var(--amber); }

/* depot tiles */
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 10px; }
.tile { position: relative; display: grid; grid-template-columns: auto 1fr auto; gap: 2px 12px;
  align-items: center; margin: 0; padding: 12px 14px; cursor: pointer;
  background: var(--well); border: 1px solid var(--line); border-radius: 2px;
  font: 13px/1.35 var(--sans); letter-spacing: 0; text-transform: none; color: var(--muted);
  transition: border-color 0.15s, background 0.15s, transform 0.15s; }
.tile::before { content: none; }
.tile:hover { border-color: var(--line-hot); transform: translateY(-1px); }
.tile input { grid-row: span 2; }
.tile-name { font: 600 13px/1.3 var(--mono); color: var(--text); letter-spacing: 0.04em; }
.tile-sub { grid-column: 2; font-size: 12px; }
.badge { grid-row: 1 / span 2; grid-column: 3; font: 600 9.5px/1 var(--mono);
  letter-spacing: 0.16em; padding: 4px 6px; border-radius: 1px; color: var(--muted);
  border: 1px solid var(--line-hot); }
.badge.llm { color: var(--magenta); border-color: rgba(255, 61, 139, 0.45); }
.tile:has(input:checked) { border-color: rgba(45, 226, 230, 0.55);
  background: linear-gradient(90deg, rgba(45, 226, 230, 0.1), rgba(0, 0, 0, 0.72) 70%); }
.tile:has(input:checked) .tile-name { color: var(--cyan); }

/* budget gauge */
.gauge { display: block; width: 100%; height: auto; margin: 6px 0 8px; }
.gauge .bed { fill: rgba(0, 0, 0, 0.8); stroke: var(--line); }
.gauge .luna { fill: var(--cyan); }
.gauge .sol { fill: var(--magenta); }
.gauge .tick { stroke: rgba(232, 233, 236, 0.22); stroke-width: 1; }
.gauge text { fill: var(--dim); font: 9px var(--mono); letter-spacing: 0.1em; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 18px; font: 12px/1.4 var(--mono);
  color: var(--muted); }
.legend span::before { content: ""; display: inline-block; width: 10px; height: 10px;
  margin-right: 7px; vertical-align: -1px; border-radius: 1px; background: var(--dim); }
.legend .l-luna::before { background: var(--cyan); box-shadow: 0 0 8px var(--cyan); }
.legend .l-sol::before { background: var(--magenta); box-shadow: 0 0 8px var(--magenta); }
.legend .l-free::before { background: repeating-linear-gradient(45deg, var(--line-hot) 0 2px,
  transparent 2px 4px); }

/* buttons */
button { position: relative; overflow: hidden; margin-top: 20px; padding: 12px 24px; border: 0;
  cursor: pointer; font: 700 12.5px/1 var(--mono); letter-spacing: 0.16em;
  text-transform: uppercase; color: #001318;
  background: linear-gradient(100deg, var(--cyan), var(--blue));
  clip-path: polygon(10px 0, 100% 0, 100% calc(100% - 10px), calc(100% - 10px) 100%, 0 100%,
    0 10px);
  transition: filter 0.15s, transform 0.1s; }
button::after { content: ""; position: absolute; top: 0; bottom: 0; left: 0; width: 40%;
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.55), transparent);
  transform: translateX(-120%) skewX(-20deg); }
button:hover { filter: brightness(1.12) drop-shadow(0 0 12px rgba(45, 226, 230, 0.55)); }
button:hover::after { animation: sheen 0.8s ease-out; }
button:active { transform: translateY(1px); }
button:focus-visible { outline: 2px solid #fff; outline-offset: 3px; }
button.alt { color: #1a0010; background: linear-gradient(100deg, var(--magenta), #ff7a59); }
button.quiet { margin: 0; padding: 7px 12px; color: var(--muted); background: transparent;
  border: 1px solid var(--line-hot); clip-path: none; border-radius: 2px;
  font-size: 11px; letter-spacing: 0.14em; }
button.quiet:hover { color: var(--magenta); border-color: var(--magenta); filter: none; }
.actions { display: flex; flex-wrap: wrap; align-items: center; gap: 16px; }
.actions .hint { flex: 1; min-width: 220px; margin: 20px 0 0; }

/* messages */
.msg { position: relative; display: flex; gap: 12px; align-items: center; margin-bottom: 20px;
  padding: 12px 14px 12px 16px; border: 1px solid var(--line); border-radius: 2px;
  background: var(--panel); font: 13px/1.45 var(--mono); animation: rise 0.35s ease-out both; }
.msg::before { font-size: 12px; }
.msg.ok { color: var(--lime); border-color: rgba(107, 255, 154, 0.45);
  box-shadow: inset 3px 0 0 var(--lime), 0 0 26px -10px var(--lime); }
.msg.ok::before { content: "\\25CF"; }
.msg.bad { color: var(--bad); border-color: rgba(255, 92, 122, 0.5);
  box-shadow: inset 3px 0 0 var(--bad), 0 0 26px -10px var(--bad); }
.msg.bad::before { content: "\\25B2"; }

/* ---------------------------------------------------------------- login / setup */
.auth { display: grid; grid-template-columns: 1fr minmax(300px, 430px); gap: 34px;
  align-items: center; min-height: calc(100vh - 230px); }
.scope { display: flex; flex-direction: column; align-items: center; gap: 18px; }
.radar { width: min(340px, 80vw); height: auto; }
.radar .ring { fill: none; stroke: rgba(45, 226, 230, 0.25); stroke-width: 1; }
.radar .ring.dash { stroke-dasharray: 2 6; }
.radar .cross { stroke: rgba(45, 226, 230, 0.16); stroke-width: 1; }
.radar .sweep { transform-box: view-box; transform-origin: 50% 50%;
  animation: spin 4.5s linear infinite; }
.radar .outer { fill: none; stroke: rgba(255, 61, 139, 0.45); stroke-width: 1.4;
  stroke-dasharray: 1 7; transform-box: view-box; transform-origin: 50% 50%;
  animation: spin 40s linear infinite reverse; }
.radar .blip { fill: var(--cyan); }
.radar .blip.warm { fill: var(--magenta); }
.radar .ping { fill: none; stroke: var(--cyan); stroke-width: 1.2;
  animation: ping 2.4s ease-out infinite; }
.radar .ping.warm { stroke: var(--magenta); animation-delay: -1.2s; }
.radar text { fill: var(--dim); font: 8px var(--mono); letter-spacing: 0.12em; }
.console { width: min(360px, 90vw); padding: 14px 16px; background: rgba(0, 0, 0, 0.82);
  border: 1px solid var(--line); border-radius: 2px; font: 12px/1.75 var(--mono);
  color: var(--muted); }
.console div { display: flex; gap: 8px; }
.console div::before { content: ">"; color: var(--cyan); }
.console .dots { flex: 1; overflow: hidden; color: var(--dim); white-space: nowrap; }
.console .dots::after { content: " ........................................"; }
.console b { font-weight: 600; color: var(--lime); }
.console b.amber { color: var(--amber); }
.console .cursor::after { content: "_"; color: var(--cyan); animation: caret 1s steps(1) infinite; }
.auth .panel h2 { font-size: 16px; }
.auth .lead { color: var(--muted); font-size: 13.5px; }
.auth button { width: 100%; }
@media (max-width: 860px) {
  .auth { grid-template-columns: 1fr; min-height: 0; gap: 22px; }
  .scope { order: 2; }
  .radar { width: min(240px, 70vw); }
  header { padding: 10px 16px; }
  .status .chip.net { display: none; }
  .readouts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
  .price { stroke-dashoffset: 0; }
}
"""
