type InterviewMedia = { file: File; path: string; title: string; date: string };

// Keep this in sync with Store.add_media. Browser MIME types may be empty or generic.
const supportedMedia = /\.(?:wav|mp3|mp4|m4a|mov|webm|mkv|aac|aiff|aif|flac|ogg)$/i;

function interviewDate(name: string): string {
  const match = name.match(/(?:^|\D)(\d{4})\s*(?:年|[./_-])\s*(\d{1,2})\s*(?:月|[./_-])\s*(\d{1,2})(?!\d)/);
  if (!match) return '';
  const [, year, month, day] = match;
  const iso = `${year}-${month.padStart(2, '0')}-${day.padStart(2, '0')}`;
  const date = new Date(`${iso}T00:00:00Z`);
  return year !== '0000' && Number.isFinite(date.getTime()) && date.toISOString().startsWith(iso) ? iso : '';
}

export function collectInterviewMedia(files: FileList | File[]): InterviewMedia[] {
  const entries = [];
  const folderCounts = new Map<string, number>();
  const stemCounts = new Map<string, number>();
  for (const file of Array.from(files)) {
    const path = file.webkitRelativePath || file.name;
    const parts = path.split('/');
    if (parts.length > 3 || parts.some((part) => !part || part.startsWith('.'))) continue;
    const directories = parts.slice(0, -1);
    if (directories.includes('__MACOSX') || !supportedMedia.test(file.name)) continue;
    const stem = file.name.replace(supportedMedia, '');

    const folder = directories.join('/');
    const stemPath = path.replace(supportedMedia, '');
    folderCounts.set(folder, (folderCounts.get(folder) ?? 0) + 1);
    stemCounts.set(stemPath, (stemCounts.get(stemPath) ?? 0) + 1);
    entries.push({ file, path, folder, folderName: directories.at(-1) ?? '', stem, stemPath });
  }

  entries.sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
  return entries.map(({ file, path, folder, folderName, stem, stemPath }) => {
    const name = stemCounts.get(stemPath)! > 1 ? file.name : stem;
    const title = folderName && folderCounts.get(folder)! > 1 ? `${folderName} · ${name}` : folderName || name;
    // A folder and filename can each fit on disk while their combined title exceeds the library limit.
    const chars = Array.from(title);
    const displayTitle = chars.length > 240
      ? chars.slice(0, 129).join('') + '…' + chars.slice(-110).join('')
      : title;
    return { file, path, title: displayTitle, date: interviewDate(folderName || stem) };
  });
}
