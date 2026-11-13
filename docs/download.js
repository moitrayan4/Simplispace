const out = document.getElementById('result');
const RELEASES = 'https://github.com/moitrayan4/Simplispace/releases';
const show = (kind, html) => { out.innerHTML = `<div class="status ${kind}">${html}</div>`; };
const esc = s => String(s).replace(/[&<>"']/g, c => `&#${c.charCodeAt(0)};`);

// The list endpoint answers [] (not 404) when nothing is released, so no console error.
fetch('https://api.github.com/repos/moitrayan4/Simplispace/releases?per_page=5')
  .then(r => {
    if (!r.ok) throw new Error(`GitHub answered ${r.status}`);
    return r.json();
  })
  .then(list => {
    const rel = list.find(r => !r.draft && !r.prerelease);
    const exe = rel && rel.assets.find(a => a.name.toLowerCase().endsWith('.exe'));
    if (!exe) {
      show('empty', `<strong>No download yet.</strong> Simplispace is in private testing and the first
        public version isn’t out yet. Want early access? Email
        <a href="mailto:moitrayannath444@gmail.com?subject=Simplispace%20early%20access">moitrayannath444@gmail.com</a>.`);
      return;
    }
    const mb = (exe.size / 1048576).toFixed(0);
    show('ok', `<strong>Version ${esc(rel.tag_name)} is ready.</strong> ${mb} MB, released
      ${new Date(rel.published_at).toLocaleDateString()}.`);
    out.insertAdjacentHTML('beforeend',
      `<a class="btn" href="${esc(exe.browser_download_url)}">Download ${esc(exe.name)}</a>`);
  })
  .catch(() => {
    show('err', `<strong>Couldn’t check for the latest version.</strong> Check your internet connection
      and <a href="">try again</a>, or get it straight from
      <a href="${RELEASES}">GitHub Releases</a>.`);
  });
