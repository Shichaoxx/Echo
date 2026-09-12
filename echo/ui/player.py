"""Native Streamlit audio plus client-side seek and rate controls; no rerun per tick."""
from pathlib import Path
import streamlit as st

ASSETS = Path(__file__).resolve().parents[2] / 'assets'
def get_controls():
    return st.components.v2.component(
        'echo_audio_controls', isolate_styles=False,
        html='''<div class="echo-controls">
          <button data-action="play" type="button" aria-label="播放">播放</button>
          <input type="range" min="0" max="0" step="0.1" value="0" aria-label="拖动回听位置" disabled>
          <output aria-live="off"></output>
          <div><button data-action="back" type="button">后退 10 秒</button>
          <button data-action="forward" type="button">前进 10 秒</button>
          <label>倍速 <select aria-label="播放倍速"><option>0.75</option><option selected>1</option><option>1.25</option><option>1.5</option><option>2</option></select></label></div>
          <p role="status"></p></div>''',
        css='''.echo-controls{color:var(--ink);font-size:12px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}.echo-controls input{flex:1;min-width:90px;accent-color:var(--accent)}
        .echo-controls output{font-variant-numeric:tabular-nums;color:var(--muted)}
        .echo-controls>div{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
        .echo-controls button,.echo-controls select{padding:6px;border:1px solid var(--line);border-radius:7px;background:var(--panel);color:var(--ink);cursor:pointer}
        .echo-controls button[data-action=play]{background:var(--ink);color:var(--panel);border-radius:30px;min-width:52px;padding:10px}
        .echo-controls button:disabled{opacity:.4}.echo-controls p{font-size:12px;margin:0}.echo-controls p:empty{display:none}''',
        js=(ASSETS / 'player_controls.js').read_text(),
    )



def render_player(path, *, key, seek=0, offset=0, token=0, autoplay=False):
    container = 'player-' + key
    with st.container(key=container):
        # Keep start_time stable: ordinary page reruns must not reset playback.
        st.audio(str(path), format='audio/mpeg', start_time=0)
        get_controls()(data=dict(container=container, seek=max(0, seek), offset=offset,
                           token=token, autoplay=autoplay), key='controls-' + key)
