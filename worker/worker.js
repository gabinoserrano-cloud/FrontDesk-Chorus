/**
 * Chorus Front Desk — sync proxy
 *
 * Holds the Supabase credentials so index.html doesn't have to. The dashboard calls
 * these five endpoints; this Worker signs in to Supabase and forwards the request.
 *
 * Nothing secret is in this file — the four values below come from Worker secrets
 * (see worker/DEPLOY.md). This file is safe to commit to a public repo.
 *
 *   GET  /api/data          -> { data, updated_at } | null      full row
 *   GET  /api/data?meta=1   -> { updated_at } | null            ~60-byte poll probe
 *   PUT  /api/data          <- { data, expected }               compare-and-swap write
 *                           -> { updated_at } | null            null = lost the race
 *   GET  /api/inbox         -> { data } | null                  triage queue row
 *   PUT  /api/inbox         <- { data }                         204, clears the queue
 *
 * The compare-and-swap contract is preserved exactly: `expected` becomes a PostgREST
 * updated_at filter, so a stale write matches zero rows and returns null instead of
 * clobbering whoever wrote first. The client's three-way merge is unchanged.
 */

let session = null; // cached per isolate; falls back to a fresh sign-in when stale

async function token(env) {
  if (session && session.access_token) {
    const expiresAt = (session.created_at ? session.created_at * 1000 : 0)
      + ((session.expires_in || 3600) - 60) * 1000;
    if (Date.now() < expiresAt) return session.access_token;
  }
  const res = await fetch(env.SUPA_URL + '/auth/v1/token?grant_type=password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'apikey': env.SUPA_KEY },
    body: JSON.stringify({ email: env.SUPA_SYNC_EMAIL, password: env.SUPA_SYNC_PASS })
  });
  if (!res.ok) throw new Error('auth ' + res.status);
  session = await res.json();
  return session.access_token;
}

async function hdrs(env, extra) {
  return Object.assign({
    'Content-Type': 'application/json',
    'apikey': env.SUPA_KEY,
    'Authorization': 'Bearer ' + (await token(env))
  }, extra || {});
}

const json = (body, status) => new Response(
  body === null ? 'null' : JSON.stringify(body),
  { status: status || 200, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } }
);

async function rest(env, path, init) {
  const res = await fetch(env.SUPA_URL + '/rest/v1/' + path, init);
  if (!res.ok) throw new Error('supabase ' + res.status);
  return res;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, '');
    const method = request.method;

    try {
      // ── main row ────────────────────────────────────────────────────────────
      if (path === '/api/data' && method === 'GET') {
        const meta = url.searchParams.get('meta') === '1';
        const select = meta ? 'updated_at' : 'data,updated_at';
        const res = await rest(env, 'fd_data?id=eq.main&select=' + select, { headers: await hdrs(env) });
        const rows = await res.json();
        return json(rows[0] || null);
      }

      if (path === '/api/data' && method === 'PUT') {
        const body = await request.json();
        if (!body || typeof body.data === 'undefined') return json({ error: 'data required' }, 400);
        let q = 'fd_data?id=eq.main&select=updated_at';
        if (body.expected) q += '&updated_at=eq.' + encodeURIComponent(body.expected);
        const res = await rest(env, q, {
          method: 'PATCH',
          headers: await hdrs(env, { 'Prefer': 'return=representation' }),
          body: JSON.stringify({ data: body.data, updated_at: new Date().toISOString() })
        });
        const rows = await res.json();
        // [] means the updated_at filter matched nothing — another device wrote first.
        return json(Array.isArray(rows) && rows.length ? rows[0] : null);
      }

      // ── triage inbox row ────────────────────────────────────────────────────
      if (path === '/api/inbox' && method === 'GET') {
        const res = await rest(env, 'fd_data?id=eq.todo-inbox&select=data', { headers: await hdrs(env) });
        const rows = await res.json();
        return json(rows[0] || null);
      }

      if (path === '/api/inbox' && method === 'PUT') {
        const body = await request.json();
        if (!body || typeof body.data === 'undefined') return json({ error: 'data required' }, 400);
        await rest(env, 'fd_data?id=eq.todo-inbox', {
          method: 'PATCH',
          headers: await hdrs(env, { 'Prefer': 'return=minimal' }),
          body: JSON.stringify({ data: body.data, updated_at: new Date().toISOString() })
        });
        return new Response(null, { status: 204 });
      }

      return json({ error: 'not found' }, 404);
    } catch (err) {
      // Never echo the upstream body — it can carry credential hints.
      return json({ error: String(err.message || err) }, 502);
    }
  }
};
