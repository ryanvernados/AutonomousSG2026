-- Existing service_role key is backend-only. Mutations stay inside the transaction RPC.
grant select on public.projects, public.project_members, public.airports, public.scenarios,
    public.scenario_flights, public.simulation_sessions, public.session_checkpoints,
    public.instructions, public.proposals, public.reservations, public.negotiations,
    public.agent_messages, public.validation_results, public.approvals, public.audit_events,
    public.session_commands, public.session_creations to service_role;
