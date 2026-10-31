// Actual PostgreSQL engine (WASM), not SQL string matching. No Supabase account required.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { PGlite } from '@electric-sql/pglite';

const fixture = spawnSync('uv', ['run', 'python', '-m', 'scripts.sql_fixture'], {
  encoding: 'utf8', maxBuffer: 20 * 1024 * 1024,
});
assert.equal(fixture.status, 0, fixture.stderr || String(fixture.error));
const commits = JSON.parse(fixture.stdout);
const pg = new PGlite();
let assertions = 0;
function verify(value, message) { assert.ok(value, message); assertions++; }
async function scalar(query, params = []) { return (await pg.query(query, params)).rows[0].value; }
async function fails(operation, message) {
  await assert.rejects(operation, undefined, message);
  assertions++;
}
try {
  await pg.exec(`
    create role anon;
    create role authenticated;
    create role service_role bypassrls;
    create schema auth;
    create table auth.users(id uuid primary key);
    create function auth.uid() returns uuid language sql stable as
      $$ select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid $$;
    grant usage on schema auth to authenticated;
    grant execute on function auth.uid() to authenticated;
    insert into auth.users values
      ('00000000-0000-0000-0000-000000000001'),
      ('00000000-0000-0000-0000-000000000002'),
      ('00000000-0000-0000-0000-000000000003');
  `);
  await pg.exec(await readFile('supabase/migrations/202610310001_platform.sql', 'utf8'));
  await pg.exec(await readFile('supabase/migrations/202610310002_https_backend_reads.sql', 'utf8'));
  await pg.exec(await readFile('supabase/seed.sql', 'utf8'));
  await pg.exec(`
    insert into public.project_members values
      ('00000000-0000-0000-0000-000000000010','00000000-0000-0000-0000-000000000001','controller'),
      ('00000000-0000-0000-0000-000000000010','00000000-0000-0000-0000-000000000002','viewer');
    insert into public.projects values('00000000-0000-0000-0000-000000000020','Other project');
    insert into public.project_members values
      ('00000000-0000-0000-0000-000000000020','00000000-0000-0000-0000-000000000003','admin');
  `);
  async function commit(c) {
    return scalar('select public.commit_simulation_session($1,$2,$3,$4,$5,$6,$7,$8,$9) as value', [
      c.document, c.expected_revision, c.events, c.actor_id, c.command_id, c.fingerprint, c.response,
      c.creation, c.internal]);
  }
  const [create, start, propose, approve] = commits;
  const sid = create.document.session_id;
  await commit(create);
  verify((await commit(create)).session_id === sid, 'Creation retry must be idempotent');
  await commit(start);
  await commit(propose);
  const before = await scalar('select document as value from public.simulation_sessions where session_id=$1', [sid]);
  const approvalWithFailure = structuredClone(approve);
  approvalWithFailure.events[0].event_id = create.events[0].event_id; // Late unique violation after all plan writes.
  await fails(() => commit(approvalWithFailure), 'Late transaction failure must roll everything back');
  assert.deepEqual(await scalar('select document as value from public.simulation_sessions where session_id=$1', [sid]), before);
  assertions++;
  verify(await scalar('select count(*)::int as value from public.approvals') === 0, 'No partial approval');
  verify(await scalar('select count(*)::int as value from public.reservations') === 1, 'No partial reservations');
  await commit(approve);
  verify(await scalar('select state_version::int as value from public.simulation_sessions where session_id=$1', [sid]) === 1,
         'Atomic approval advances operational version');
  verify(await scalar('select count(*)::int as value from public.approvals') === 1, 'Exactly one approval');
  verify(await scalar('select count(*)::int as value from public.audit_events') === 4, 'Ordered durable events');
  assert.deepEqual(await commit(approve), approve.response); assertions++;
  verify(await scalar('select count(*)::int as value from public.audit_events') === 4, 'Retry does not duplicate audit');
  const changed = structuredClone(approve);
  changed.fingerprint = 'different';
  await fails(() => commit(changed), 'Changed command cannot reuse ID');
  const stale = structuredClone(approve);
  stale.command_id = 'other-stale';
  await fails(() => commit(stale), 'Stale database revision rejected');
  const forbidden = structuredClone(create);
  forbidden.command_id = 'outsider-create';
  forbidden.actor_id = '00000000-0000-0000-0000-000000000003';
  await fails(() => commit(forbidden), 'Admin of another project cannot create this session');
  await fails(() => pg.exec('update public.audit_events set document=document'), 'Audit immutable even for accidental admin update');
  await fails(() => pg.exec(`update public.proposals set document=jsonb_set(document,'{plan,source}','"MANUAL"')`),
              'Plan content cannot change after publication');
  for (const [user, expected] of [
    ['00000000-0000-0000-0000-000000000001', 1],
    ['00000000-0000-0000-0000-000000000002', 1],
    ['00000000-0000-0000-0000-000000000003', 0],
  ]) {
    await pg.query("select set_config('request.jwt.claim.sub',$1,false)", [user]);
    await pg.exec('set role authenticated');
    verify(await scalar('select count(*)::int as value from public.simulation_sessions') === expected, 'Project RLS isolates reads');
    verify(await scalar('select count(*)::int as value from public.audit_events') === expected * 4, 'Audit RLS isolates reads');
    await fails(() => pg.exec('delete from public.reservations'), 'Browser cannot mutate operational reservations');
    await fails(() => commit(approve), 'Browser cannot invoke privileged transaction function');
    await pg.exec('reset role');
  }
  verify(await scalar('select count(*)::int as value from public.session_checkpoints') === 4,
         'Every durable revision has a recovery checkpoint');
  console.log(`${assertions} PostgreSQL assertions passed: migrations, seed, RLS, immutable plans, atomicity, retry and recovery.`);
} finally {
  await pg.close();
}
