export default function({parentElement, data}) {
  const scope = parentElement.closest('.st-key-' + data.container);
  const audio = scope?.querySelector('audio');
  const controls = parentElement.querySelector('.echo-controls');
  if (!audio || !controls) return;
  const play = controls.querySelector('[data-action="play"]');
  const dock = scope.closest('.st-key-playback-dock');
  const main = dock?.closest('.block-container');
  const position = () => {
    if(!dock || !main) return;
    const rect = main.getBoundingClientRect();
    const style = getComputedStyle(main);
    const left = parseFloat(style.paddingLeft), right = parseFloat(style.paddingRight);
    dock.style.left = (rect.left + left) + 'px';
    dock.style.width = Math.max(200, rect.width - left - right) + 'px';
  };
  const resize = new ResizeObserver(position); if(main) resize.observe(main);
  window.addEventListener('resize',position); position();
  // Hide native chrome only after the enhanced controls are ready; keep native fallback.
  const native = audio.closest('[data-testid="stAudio"]');
  if(native) native.style.display='none';
  const back = controls.querySelector('[data-action="back"]');
  const forward = controls.querySelector('[data-action="forward"]');
  const rate = controls.querySelector('select');
  const status = controls.querySelector('[role="status"]');
  const progress = controls.querySelector('input');
  const clock = controls.querySelector('output');
  const fmt = t => {t = Math.max(0,Math.floor(t || 0)); return [Math.floor(t/3600),Math.floor(t/60)%60,t%60].map(n=>String(n).padStart(2,'0')).join(':');};
  const seek = t => {if(Number.isFinite(audio.duration)) audio.currentTime = Math.max(0,Math.min(t,audio.duration));};
  const update = () => {
    play.textContent = audio.paused ? '播放' : '暂停';
    play.setAttribute('aria-label',audio.paused ? '播放' : '暂停');
    const valid = Number.isFinite(audio.duration) && audio.duration > 0;
    back.disabled = forward.disabled = progress.disabled = !valid;
    progress.max = valid ? audio.duration : 0;
    progress.value = audio.currentTime || 0;
    clock.textContent = fmt((audio.currentTime || 0) + data.offset) + ' / ' + fmt((audio.duration || 0) + data.offset);
  };
  const apply = () => {
    audio.playbackRate = Number(audio.dataset.echoRate || 1);
    rate.value = String(audio.playbackRate);
    if (audio.dataset.echoSeek !== String(data.token)) {
      seek(data.seek);
      audio.dataset.echoSeek = String(data.token);
      if(data.autoplay) audio.play().catch(()=>status.textContent='已定位，请点击播放器的播放按钮。');
    }
    update();
  };
  const onBack = () => seek(audio.currentTime - 10);
  const onForward = () => seek(audio.currentTime + 10);
  const onRate = () => {audio.dataset.echoRate = rate.value; audio.playbackRate = Number(rate.value);};
  const onSeek = () => seek(Number(progress.value));
  const onPlay = () => {if(audio.paused) audio.play().catch(()=>status.textContent='无法开始播放，请重试或下载音频。');else audio.pause();};
  const onExternalSeek = event => {
    if(event.detail?.player !== data.container) return;
    const time = Number(event.detail.time);
    if(!Number.isFinite(time)) return;
    if(audio.readyState < 1) {status.textContent='音频正在载入，请稍后再点击时间戳。';return;}
    seek(time); audio.play().catch(()=>status.textContent='已定位，请点击播放。');
  };
  const onError = () => {status.textContent='音频载入失败，请刷新页面或下载音频回听。'; update();};
  back.addEventListener('click',onBack); forward.addEventListener('click',onForward);
  play.addEventListener('click',onPlay); document.addEventListener('echo:seek',onExternalSeek);
  audio.addEventListener('play',update); audio.addEventListener('pause',update);
  rate.addEventListener('change',onRate); progress.addEventListener('input',onSeek);
  audio.addEventListener('loadedmetadata',apply); audio.addEventListener('timeupdate',update); audio.addEventListener('error',onError);
  if(audio.readyState >= 1) apply(); else update();
  return () => {
    resize.disconnect();window.removeEventListener('resize',position);
    play.removeEventListener('click',onPlay);document.removeEventListener('echo:seek',onExternalSeek);
    audio.removeEventListener('play',update);audio.removeEventListener('pause',update);
    if(native) native.style.display='';
    back.removeEventListener('click',onBack); forward.removeEventListener('click',onForward);
    rate.removeEventListener('change',onRate); progress.removeEventListener('input',onSeek);
    audio.removeEventListener('loadedmetadata',apply); audio.removeEventListener('timeupdate',update); audio.removeEventListener('error',onError);
  };
}
