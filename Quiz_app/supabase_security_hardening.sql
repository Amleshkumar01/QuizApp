-- ═══════════════════════════════════════════════════════════════════════════════
-- PlacementIQ — Supabase Security Hardening Migration (Final)
-- ═══════════════════════════════════════════════════════════════════════════════
--
-- PURPOSE:
--   Lock down all 18 Django-private tables AND 17 Django-owned sequences in the
--   public schema so they cannot be accessed through the Supabase Data API.
--   Django (connected as 'postgres' table-owner role) continues working normally.
--
-- TABLES AFFECTED (18): app1_answer, app1_attempt, app1_category, app1_company,
--   app1_option, app1_question, app1_quiz, app1_supabaseusermapping, auth_group,
--   auth_group_permissions, auth_permission, auth_user, auth_user_groups,
--   auth_user_user_permissions, django_admin_log, django_content_type,
--   django_migrations, django_session
--
-- SEQUENCES AFFECTED (17): app1_answer_id_seq, app1_attempt_id_seq,
--   app1_category_id_seq, app1_company_id_seq, app1_option_id_seq,
--   app1_question_id_seq, app1_quiz_id_seq, app1_supabaseusermapping_id_seq,
--   auth_group_id_seq, auth_group_permissions_id_seq, auth_permission_id_seq,
--   auth_user_groups_id_seq, auth_user_id_seq, auth_user_user_permissions_id_seq,
--   django_admin_log_id_seq, django_content_type_id_seq, django_migrations_id_seq
--
-- NOT MODIFIED: public.profiles, auth.users, storage, triggers, RPCs, policies
-- NOT USED: DROP, DELETE, TRUNCATE, CASCADE, FORCE ROW LEVEL SECURITY
-- ═══════════════════════════════════════════════════════════════════════════════

BEGIN;

-- ┌──────────────────────────────────────────────────────────────────────────┐
-- │ 1. REVOKE ALL on Django-private TABLES                                   │
-- └──────────────────────────────────────────────────────────────────────────┘

REVOKE ALL ON TABLE public.app1_answer FROM anon, authenticated, service_role;
REVOKE ALL ON TABLE public.app1_attempt FROM anon, authenticated, service_role;
REVOKE ALL ON TABLE public.app1_category FROM anon, authenticated, service_role;
REVOKE ALL ON TABLE public.app1_company FROM anon, authenticated, service_role;
REVOKE ALL ON TABLE public.app1_option FROM anon, authenticated, service_role;
REVOKE ALL ON TABLE public.app1_question FROM anon, authenticated, service_role;
REVOKE ALL ON TABLE public.app1_quiz FROM anon, authenticated, service_role;
REVOKE ALL ON TABLE public.app1_supabaseusermapping FROM anon, authenticated, service_role;
REVOKE ALL ON TABLE public.auth_group FROM anon, authenticated, service_role;
REVOKE ALL ON TABLE public.auth_group_permissions FROM anon, authenticated, service_role;
REVOKE ALL ON TABLE public.auth_permission FROM anon, authenticated, service_role;
REVOKE ALL ON TABLE public.auth_user FROM anon, authenticated, service_role;
REVOKE ALL ON TABLE public.auth_user_groups FROM anon, authenticated, service_role;
REVOKE ALL ON TABLE public.auth_user_user_permissions FROM anon, authenticated, service_role;
REVOKE ALL ON TABLE public.django_admin_log FROM anon, authenticated, service_role;
REVOKE ALL ON TABLE public.django_content_type FROM anon, authenticated, service_role;
REVOKE ALL ON TABLE public.django_migrations FROM anon, authenticated, service_role;
REVOKE ALL ON TABLE public.django_session FROM anon, authenticated, service_role;

-- ┌──────────────────────────────────────────────────────────────────────────┐
-- │ 2. REVOKE ALL on Django-private SEQUENCES                                │
-- └──────────────────────────────────────────────────────────────────────────┘

REVOKE ALL ON SEQUENCE public.app1_answer_id_seq FROM anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.app1_attempt_id_seq FROM anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.app1_category_id_seq FROM anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.app1_company_id_seq FROM anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.app1_option_id_seq FROM anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.app1_question_id_seq FROM anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.app1_quiz_id_seq FROM anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.app1_supabaseusermapping_id_seq FROM anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.auth_group_id_seq FROM anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.auth_group_permissions_id_seq FROM anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.auth_permission_id_seq FROM anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.auth_user_groups_id_seq FROM anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.auth_user_id_seq FROM anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.auth_user_user_permissions_id_seq FROM anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.django_admin_log_id_seq FROM anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.django_content_type_id_seq FROM anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.django_migrations_id_seq FROM anon, authenticated, service_role;

-- ┌──────────────────────────────────────────────────────────────────────────┐
-- │ 3. ENABLE ROW LEVEL SECURITY on each Django-private table                │
-- │    No policies defined → anon/authenticated see zero rows.               │
-- │    Owner (postgres) is exempt (no FORCE ROW LEVEL SECURITY).             │
-- └──────────────────────────────────────────────────────────────────────────┘

ALTER TABLE public.app1_answer ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.app1_attempt ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.app1_category ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.app1_company ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.app1_option ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.app1_question ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.app1_quiz ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.app1_supabaseusermapping ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.auth_group ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.auth_group_permissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.auth_permission ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.auth_user ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.auth_user_groups ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.auth_user_user_permissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.django_admin_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.django_content_type ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.django_migrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.django_session ENABLE ROW LEVEL SECURITY;

-- ┌──────────────────────────────────────────────────────────────────────────┐
-- │ 4. ALTER DEFAULT PRIVILEGES for future tables/sequences created by        │
-- │    the 'postgres' role — prevent auto-grant to Supabase API roles.        │
-- └──────────────────────────────────────────────────────────────────────────┘

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    REVOKE ALL ON TABLES FROM anon, authenticated, service_role;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    REVOKE ALL ON SEQUENCES FROM anon, authenticated, service_role;

COMMIT;
