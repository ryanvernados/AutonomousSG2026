-- Contract v1. Backend owns writes; browser roles have project-scoped SELECT only.

create table public.projects (
    project_id uuid primary key,
    name text not null
);
create table public.project_members (
    project_id uuid not null references public.projects,
    user_id uuid not null references auth.users,
    role text not null check (role in ('viewer','controller','admin')),
    primary key(project_id,user_id)
);
create function public.is_project_member(p_project uuid) returns boolean
language sql stable security definer set search_path = '' as $$
    select exists(select 1 from public.project_members
                  where project_id=p_project and user_id=auth.uid())
$$;
revoke all on function public.is_project_member(uuid) from public,anon;
grant execute on function public.is_project_member(uuid) to authenticated;

create table public.airports (
    project_id uuid not null references public.projects,
    airport_iata text not null,
    document jsonb not null,
    primary key(project_id,airport_iata)
);
create table public.scenarios (
    scenario_id text primary key,
    project_id uuid not null references public.projects,
    document jsonb not null
);
create table public.scenario_flights (
    scenario_id text not null references public.scenarios,
    flight_id text not null,
    project_id uuid not null references public.projects,
    document jsonb not null,
    primary key(scenario_id,flight_id)
);
create table public.simulation_sessions (
    session_id text primary key,
    project_id uuid not null references public.projects,
    scenario_id text not null references public.scenarios,
    revision bigint not null,
    state_version bigint not null,
    document jsonb not null
);
create table public.session_checkpoints (
    session_id text not null references public.simulation_sessions,
    project_id uuid not null references public.projects,
    revision bigint not null,
    document jsonb not null,
    recorded_at_utc timestamptz not null default now(),
    primary key(session_id,revision)
);
create table public.instructions (
    instruction_id text not null,
    session_id text not null references public.simulation_sessions,
    project_id uuid not null references public.projects,
    document jsonb not null,
    primary key(session_id,instruction_id)
);
create table public.proposals (
    proposal_id text not null,
    session_id text not null references public.simulation_sessions,
    project_id uuid not null references public.projects,
    plan_hash text not null,
    document jsonb not null,
    primary key(session_id,proposal_id)
);
create table public.reservations (
    reservation_id text not null,
    session_id text not null references public.simulation_sessions,
    project_id uuid not null references public.projects,
    resource_id text not null,
    flight_id text not null,
    starts_at_utc timestamptz not null,
    ends_at_utc timestamptz not null check(ends_at_utc>starts_at_utc),
    document jsonb not null,
    primary key(session_id,reservation_id)
);
create table public.negotiations (
    negotiation_id text not null,
    session_id text not null references public.simulation_sessions,
    project_id uuid not null references public.projects,
    document jsonb not null,
    primary key(session_id,negotiation_id)
);
create table public.agent_messages (
    message_id text not null,
    session_id text not null references public.simulation_sessions,
    project_id uuid not null references public.projects,
    document jsonb not null,
    primary key(session_id,message_id)
);
create table public.validation_results (
    session_id text not null references public.simulation_sessions,
    project_id uuid not null references public.projects,
    proposal_id text not null,
    document jsonb not null,
    primary key(session_id,proposal_id)
);
create table public.approvals (
    session_id text not null references public.simulation_sessions,
    project_id uuid not null references public.projects,
    proposal_id text not null,
    document jsonb not null,
    primary key(session_id,proposal_id)
);
create table public.audit_events (
    session_id text not null references public.simulation_sessions,
    project_id uuid not null references public.projects,
    event_seq bigint not null,
    event_id text not null unique,
    document jsonb not null,
    primary key(session_id,event_seq)
);
create table public.session_commands (
    session_id text not null references public.simulation_sessions,
    command_id text not null,
    fingerprint text not null,
    response jsonb not null,
    primary key(session_id,command_id)
);
create table public.session_creations (
    actor_id text not null,
    command_id text not null,
    fingerprint text not null,
    response jsonb not null,
    primary key(actor_id,command_id)
);
create index project_members_user_idx on public.project_members(user_id,project_id);
create index simulation_sessions_project_idx on public.simulation_sessions(project_id);

alter table public.projects enable row level security;
alter table public.project_members enable row level security;
create policy member_reads_project on public.projects for select to authenticated
using(public.is_project_member(project_id));
create policy member_reads_own_membership on public.project_members for select to authenticated
using(user_id=auth.uid());
grant select on public.projects, public.project_members to authenticated;

do $$
declare t text;
begin
    foreach t in array array['airports','scenarios','scenario_flights','simulation_sessions',
        'session_checkpoints','instructions','proposals','reservations','negotiations',
        'agent_messages','validation_results','approvals','audit_events'] loop
        execute format('alter table public.%I enable row level security',t);
        execute format('create policy project_read on public.%I for select to authenticated
                        using(public.is_project_member(project_id))',t);
        execute format('revoke all on public.%I from anon,authenticated',t);
        execute format('grant select on public.%I to authenticated',t);
    end loop;
end $$;
alter table public.session_commands enable row level security;
alter table public.session_creations enable row level security;
revoke all on public.session_commands,public.session_creations from anon,authenticated;
revoke insert,update,delete on public.projects,public.project_members from anon,authenticated;

-- Prevent even accidental updates/deletes to the durable ledger and immutable plan body.
create function public.reject_ledger_mutation() returns trigger
language plpgsql set search_path = '' as $$
begin raise exception 'Append-only ledger' using errcode='42501'; end
$$;
create trigger audit_append_only before update or delete on public.audit_events
for each row execute function public.reject_ledger_mutation();
create trigger approval_append_only before update or delete on public.approvals
for each row execute function public.reject_ledger_mutation();
create function public.keep_plan_immutable() returns trigger
language plpgsql set search_path = '' as $$
begin
    if old.plan_hash<>new.plan_hash or old.document->'plan'<>new.document->'plan' then
        raise exception 'Immutable proposal content' using errcode='40001';
    end if;
    return new;
end $$;
create trigger immutable_plan before update on public.proposals
for each row execute function public.keep_plan_immutable();

create function public.commit_simulation_session(
    p_document jsonb, p_expected_revision bigint, p_events jsonb, p_actor text,
    p_command text, p_fingerprint text, p_response jsonb, p_creation boolean default false,
    p_internal boolean default false
) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
    sid text := p_document->>'session_id';
    pid uuid := (p_document->>'project_id')::uuid;
    current_row public.simulation_sessions%rowtype;
    cached record;
    rev bigint;
    seq bigint;
    item jsonb;
    entry record;
    final_doc jsonb;
begin
    -- Serialize creations too. Hash collisions only serialize unrelated work.
    perform pg_advisory_xact_lock(hashtextextended(
        case when p_creation then p_actor||':'||p_command else sid end,0));
    if not p_internal and not exists(select 1 from public.project_members
        where project_id=pid and user_id::text=p_actor and role in ('controller','admin')) then
        raise exception 'Controller membership required' using errcode='42501';
    end if;
    if p_creation then
        select fingerprint,response into cached from public.session_creations
        where actor_id=p_actor and command_id=p_command;
    else
        select fingerprint,response into cached from public.session_commands
        where session_id=sid and command_id=p_command;
    end if;
    if found then
        if cached.fingerprint<>p_fingerprint then
            raise exception 'Command ID reused' using errcode='40001';
        end if;
        return cached.response;
    end if;
    select * into current_row from public.simulation_sessions where session_id=sid for update;
    if p_expected_revision is null then
        if found then raise exception 'Session exists' using errcode='40001'; end if;
        rev := 0;
    else
        if not found or current_row.revision<>p_expected_revision or current_row.project_id<>pid
           or current_row.scenario_id<>p_document->'snapshot'->>'scenario_id' then
            raise exception 'Concurrent update' using errcode='40001';
        end if;
        -- Operational state and proposal validity are checked again INSIDE the transaction.
        if (p_document->'snapshot'->>'state_version')::bigint < current_row.state_version
           or (p_document->'snapshot'->>'state_version')::bigint > current_row.state_version+1 then
            raise exception 'Invalid state version' using errcode='40001';
        end if;
        for item in select value from jsonb_array_elements(p_document->'approvals') loop
            if not exists(select 1 from public.approvals where session_id=sid
                          and proposal_id=item->>'proposal_id') and item->>'decision'='APPROVE' then
                if not exists(select 1 from public.proposals where session_id=sid
                    and proposal_id=item->>'proposal_id' and plan_hash=item->>'plan_hash'
                    and document->>'status'='AWAITING_AUTHORIZATION'
                    and (document->'plan'->>'base_state_version')::bigint=current_row.state_version
                    and (document->'plan'->>'instruction_version')::bigint=
                        (current_row.document->'snapshot'->>'instruction_version')::bigint
                    and (document->'plan'->>'expires_at_utc')::timestamptz>clock_timestamp()) then
                    raise exception 'Stale or expired approval' using errcode='40001';
                end if;
            end if;
        end loop;
        rev := p_expected_revision+1;
    end if;
    if not exists(select 1 from public.scenarios where scenario_id=p_document->'snapshot'->>'scenario_id'
                  and project_id=pid) then
        raise exception 'Scenario project mismatch' using errcode='42501';
    end if;
    final_doc := jsonb_set(p_document,'{revision}',to_jsonb(rev));
    insert into public.simulation_sessions values(sid,pid,p_document->'snapshot'->>'scenario_id',rev,
        (p_document->'snapshot'->>'state_version')::bigint,final_doc)
    on conflict(session_id) do update set revision=excluded.revision,state_version=excluded.state_version,
        document=excluded.document;
    insert into public.session_checkpoints(session_id,project_id,revision,document) values(sid,pid,rev,final_doc);
    for item in select value from jsonb_array_elements(p_document->'snapshot'->'active_instructions') loop
        insert into public.instructions values(item->>'instruction_id',sid,pid,item)
        on conflict(session_id,instruction_id) do nothing;
    end loop;
    for entry in select * from jsonb_each(p_document->'proposals') loop
        insert into public.proposals values(entry.key,sid,pid,entry.value->'plan'->>'plan_hash',entry.value)
        on conflict(session_id,proposal_id) do update set document=excluded.document;
        insert into public.validation_results values(sid,pid,entry.key,entry.value->'checks')
        on conflict(session_id,proposal_id) do nothing;
    end loop;
    delete from public.reservations where session_id=sid;
    for item in select value from jsonb_array_elements(p_document->'snapshot'->'resource_reservations') loop
        insert into public.reservations values(item->>'reservation_id',sid,pid,item->>'resource_id',
            item->>'flight_id',(item->>'starts_at_utc')::timestamptz,(item->>'ends_at_utc')::timestamptz,item);
    end loop;
    for entry in select * from jsonb_each(p_document->'negotiations') loop
        insert into public.negotiations values(entry.key,sid,pid,entry.value)
        on conflict(session_id,negotiation_id) do update set document=excluded.document;
        for item in select value from jsonb_array_elements(entry.value->'messages') loop
            insert into public.agent_messages values(item->>'message_id',sid,pid,item)
            on conflict(session_id,message_id) do nothing;
        end loop;
    end loop;
    for item in select value from jsonb_array_elements(p_document->'approvals') loop
        insert into public.approvals values(sid,pid,item->>'proposal_id',item)
        on conflict(session_id,proposal_id) do nothing;
    end loop;
    select coalesce(max(event_seq),0) into seq from public.audit_events where session_id=sid;
    for item in select value from jsonb_array_elements(p_events) loop
        seq := seq+1;
        item := jsonb_set(item,'{event_seq}',to_jsonb(seq));
        insert into public.audit_events values(sid,pid,seq,item->>'event_id',item);
    end loop;
    insert into public.session_commands values(sid,p_command,p_fingerprint,p_response);
    if p_creation then
        insert into public.session_creations values(p_actor,p_command,p_fingerprint,p_response);
    end if;
    return p_response;
end $$;
revoke all on function public.commit_simulation_session(jsonb,bigint,jsonb,text,text,text,jsonb,boolean,boolean)
from public,anon,authenticated;
grant execute on function public.commit_simulation_session(jsonb,bigint,jsonb,text,text,text,jsonb,boolean,boolean)
to service_role;
-- Use a server-only postgres/service_role connection. No browser mutation policies exist.
