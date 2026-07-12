-- PlacementIQ — Supabase Security Hardening (Teacher Management tables)
-- ═══════════════════════════════════════════════════════════════════════════════
--
-- PURPOSE:
--   Extends supabase_security_hardening.sql to cover the NEW Django-private
--   tables (and their sequences) added by migration 0007 for the Teacher
--   Management System. Same policy as before: revoke Data-API access for anon,
--   authenticated and service_role, enable RLS, create NO anon/authenticated
--   policies. Django connects as the 'postgres' table-owner and keeps working.
--
-- NEW TABLES (10):
--   app1_teacherprofile, app1_studentprofile, app1_pendingstudentprofile,
--   app1_placementdrive, app1_placementdrive_assigned_teachers,
--   app1_company_assigned_teachers, app1_quiz_assigned_teachers,
--   app1_importbatch, app1_importedresult, app1_auditlog
--
-- NOT MODIFIED: public.profiles, auth.users, storage, triggers, RPCs, policies.
-- NOT USED: DROP, DELETE, TRUNCATE, CASCADE, FORCE ROW LEVEL SECURITY.
-- Idempotent: safe to run multiple times.
-- ═══════════════════════════════════════════════════════════════════════════════

BEGIN;

-- 1. REVOKE ALL on the new Django-private TABLES ------------------------------
REVOKE ALL ON TABLE public.app1_teacherprofile FROM anon, authenticated, service_role;
REVOKE ALL ON TABLE public.app1_studentprofile FROM anon, authenticated, service_role;
REVOKE ALL ON TABLE public.app1_pendingstudentprofile FROM anon, authenticated, service_role;
REVOKE ALL ON TABLE public.app1_placementdrive FROM anon, authenticated, service_role;
REVOKE ALL ON TABLE public.app1_placementdrive_assigned_teachers FROM anon, authenticated, service_role;
REVOKE ALL ON TABLE public.app1_company_assigned_teachers FROM anon, authenticated, service_role;
REVOKE ALL ON TABLE public.app1_quiz_assigned_teachers FROM anon, authenticated, service_role;
REVOKE ALL ON TABLE public.app1_importbatch FROM anon, authenticated, service_role;
REVOKE ALL ON TABLE public.app1_importedresult FROM anon, authenticated, service_role;
REVOKE ALL ON TABLE public.app1_auditlog FROM anon, authenticated, service_role;

-- 2. REVOKE ALL on the new Django-owned SEQUENCES -----------------------------
REVOKE ALL ON SEQUENCE public.app1_teacherprofile_id_seq FROM anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.app1_studentprofile_id_seq FROM anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.app1_pendingstudentprofile_id_seq FROM anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.app1_placementdrive_id_seq FROM anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.app1_placementdrive_assigned_teachers_id_seq FROM anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.app1_company_assigned_teachers_id_seq FROM anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.app1_quiz_assigned_teachers_id_seq FROM anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.app1_importbatch_id_seq FROM anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.app1_importedresult_id_seq FROM anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE public.app1_auditlog_id_seq FROM anon, authenticated, service_role;

-- 3. ENABLE (not FORCE) Row Level Security on each new table ------------------
--    No anon/authenticated policies are created, so the Data API cannot read
--    or write these tables. Django (postgres owner) bypasses RLS as before.
ALTER TABLE public.app1_teacherprofile ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.app1_studentprofile ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.app1_pendingstudentprofile ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.app1_placementdrive ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.app1_placementdrive_assigned_teachers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.app1_company_assigned_teachers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.app1_quiz_assigned_teachers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.app1_importbatch ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.app1_importedresult ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.app1_auditlog ENABLE ROW LEVEL SECURITY;

COMMIT;

-- VERIFY (run after commit):
--   SELECT tablename, rowsecurity FROM pg_tables
--   WHERE schemaname='public' AND tablename LIKE 'app1_%';
--   -- All new app1_* tables should show rowsecurity = true.
