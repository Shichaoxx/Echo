export default function({parentElement, data}) {
  const root = parentElement.querySelector('.echo-reader');
  const scroll = root.querySelector('.reader-scroll');
  const search = root.querySelector('input[type=search]');
  const follow = root.querySelector('[data-follow]');
  const status = root.querySelector('.reader-status');
  const previous = root.querySelector('[data-action=previous]');
  const next = root.querySelector('[data-action=next]');
  const back = root.querySelector('.reader-return');
  const fmt = t => [Math.floor(t/3600), Math.floor(t/60)%60, Math.floor(t)%60].map(n=>String(n).padStart(2,'0')).join(':');
  let audio = null, active = -1, matches = [], matchIndex = -1;
  scroll.replaceChildren();
  const rows = data.segments.map((segment, index) => {
    const row = document.createElement('div'); row.className = 'reader-row';
    const button = document.createElement('button'); button.type = 'button';
    button.textContent = fmt(segment.start); button.dataset.index = index;
    button.setAttribute('aria-label', '回听 ' + fmt(segment.start));
    const text = document.createElement('p'); text.textContent = segment.text;
    row.append(button, text); scroll.append(row); return row;
  });
  const reveal = index => {
    if (index < 0 || !rows[index]) return;
    const delta = rows[index].getBoundingClientRect().top - scroll.getBoundingClientRect().top;
    scroll.scrollTop += delta - scroll.clientHeight * .3;
  };
  const tick = () => {
    if (!audio) return;
    const time = audio.currentTime + data.offset;
    const index = data.segments.findIndex(s=>s.start <= time && time < s.end);
    if (index !== active) {
      if (active >= 0) rows[active].classList.remove('active');
      active = index;
      if (active >= 0) { rows[active].classList.add('active'); if(follow.checked) reveal(active); }
    }
  };
  const bind = () => {
    const found = document.querySelector('.st-key-' + data.player + ' audio');
    if(found === audio) return;
    audio?.removeEventListener('timeupdate', tick); audio = found;
    audio?.addEventListener('timeupdate', tick); tick();
  };
  const observer = new MutationObserver(bind); observer.observe(document.body, {childList:true, subtree:true}); bind();
  const click = e => {
    const button = e.target.closest('button[data-index]'); if(!button) return;
    const segment = data.segments[Number(button.dataset.index)];
    document.dispatchEvent(new CustomEvent('echo:seek', {detail:{player:data.player, time:segment.start-data.offset}}));
  };
  const updateSearch = () => {
    const query = search.value.trim().toLocaleLowerCase();
    matches = [];
    rows.forEach((row,i)=>{const found=!!query && data.segments[i].text.toLocaleLowerCase().includes(query);
      row.classList.toggle('match',found);row.classList.remove('current-match');if(found)matches.push(i);});
    matchIndex = -1; previous.disabled = next.disabled = !matches.length;
    status.textContent = query ? matches.length + ' 个匹配 · 保留上下文' : data.segments.length + ' 个片段 · 点击时间戳回听';
    if(matches.length) navigate(1);
  };
  const navigate = direction => {
    if(!matches.length) return;
    if(matchIndex>=0) rows[matches[matchIndex]].classList.remove('current-match');
    matchIndex = (matchIndex + direction + matches.length) % matches.length;
    rows[matches[matchIndex]].classList.add('current-match'); follow.checked=false; reveal(matches[matchIndex]);
    status.textContent = (matchIndex+1)+' / '+matches.length+' 个匹配 · 保留上下文';
  };
  const prevClick=()=>navigate(-1), nextClick=()=>navigate(1);
  const manual=()=>{follow.checked=false;};
  const returnClick=()=>{follow.checked=true;reveal(active);};
  const followChange=()=>{if(follow.checked)reveal(active);};
  scroll.addEventListener('click',click); scroll.addEventListener('wheel',manual,{passive:true});
  scroll.addEventListener('touchmove',manual,{passive:true}); scroll.addEventListener('pointerdown',manual);
  search.addEventListener('input',updateSearch); previous.addEventListener('click',prevClick); next.addEventListener('click',nextClick);
  back.addEventListener('click',returnClick); follow.addEventListener('change',followChange); updateSearch();
  return()=>{observer.disconnect();audio?.removeEventListener('timeupdate',tick);
    scroll.removeEventListener('click',click);scroll.removeEventListener('wheel',manual);scroll.removeEventListener('touchmove',manual);scroll.removeEventListener('pointerdown',manual);
    search.removeEventListener('input',updateSearch);previous.removeEventListener('click',prevClick);next.removeEventListener('click',nextClick);
    back.removeEventListener('click',returnClick);follow.removeEventListener('change',followChange);};
}
