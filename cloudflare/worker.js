// vachecker.info — Worker Cloudflare, appelé seulement pour /media/* (vidéo de
// démonstration). Les fichiers statiques de Workers renvoient toujours le
// fichier entier, même quand le navigateur n'en demande qu'un morceau (en-tête
// Range) : on ne peut pas avancer dans la vidéo, et Safari refuse de la lire.
// Ce Worker répond 206 avec le seul morceau demandé. Tout le reste du site est
// servi directement, sans passer par ici (run_worker_first, deploy_site.py).

// Taille de chaque fichier de /media : la réponse des fichiers statiques ne la
// donne pas. Remplie à l'envoi par deploy_site.py.
const SIZES = {};

export default {
  async fetch(request, env) {
    const range = request.headers.get('Range');
    const res = await env.ASSETS.fetch(request);
    if (!range || res.status !== 200 || !res.body) return withRanges(res);

    const size = Number(res.headers.get('Content-Length')) || SIZES[new URL(request.url).pathname];
    const m = /^bytes=(\d*)-(\d*)$/.exec(range.trim());
    if (!size || !m || (m[1] === '' && m[2] === '')) return withRanges(res);

    let start, end;
    if (m[1] === '') {            // « bytes=-N » : les N derniers octets
      start = Math.max(0, size - Number(m[2]));
      end = size - 1;
    } else {
      start = Number(m[1]);
      end = m[2] === '' ? size - 1 : Math.min(Number(m[2]), size - 1);
    }
    if (start >= size || start > end) {
      await res.body.cancel();
      return new Response(null, { status: 416, headers: { 'Content-Range': `bytes */${size}`, 'Accept-Ranges': 'bytes' } });
    }

    const length = end - start + 1;
    const headers = new Headers(res.headers);
    headers.set('Content-Range', `bytes ${start}-${end}/${size}`);
    headers.set('Content-Length', String(length));
    headers.set('Accept-Ranges', 'bytes');
    // Longueur annoncée exacte (Safari y tient) ; le morceau est découpé au fil du flux
    const { readable, writable } = new FixedLengthStream(length);
    slice(res.body, start, end + 1).pipeTo(writable).catch(() => {});
    return new Response(readable, { status: 206, headers });
  },
};

function withRanges(res) {
  const out = new Response(res.body, res);
  out.headers.set('Accept-Ranges', 'bytes');
  return out;
}

// Ne laisse passer que les octets [from, to) du flux, sans les recopier
function slice(body, from, to) {
  let pos = 0;
  return body.pipeThrough(new TransformStream({
    transform(chunk, ctl) {
      const a = Math.max(from - pos, 0);
      const b = Math.min(to - pos, chunk.byteLength);
      if (b > a) ctl.enqueue(chunk.subarray(a, b));
      pos += chunk.byteLength;
      if (pos >= to) ctl.terminate();
    },
  }));
}
