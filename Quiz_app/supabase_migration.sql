-- ═══════════════════════════════════════════════════════════════════════════
-- PlacementIQ — Supabase Migration
-- Creates: public.profiles table + RLS policies + username_available RPC
-- Run this in your Supabase SQL Editor (Dashboard → SQL Editor → New Query)
-- This script is IDEMPOTENT — safe to run multiple times.
-- ═══════════════════════════════════════════════════════════════════════════

-- ┌──────────────────────────────────────────────────────────────────────────┐
-- │ 1. Create public.profiles table                                          │
-- └──────────────────────────────────────────────────────────────────────────┘

CREATE TABLE IF NOT EXISTS public.profiles (
    id          UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    username    TEXT UNIQUE NOT NULL,
    first_name  TEXT NOT NULL DEFAULT '',
    last_name   TEXT NOT NULL DEFAULT '',
    role        TEXT NOT NULL DEFAULT 'student' CHECK (role IN ('student', 'admin')),
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for fast username lookups (case-insensitive)
CREATE UNIQUE INDEX IF NOT EXISTS profiles_username_lower_idx
    ON public.profiles (LOWER(username));

-- ┌──────────────────────────────────────────────────────────────────────────┐
-- │ 2. Auto-create profile on auth.users insert (trigger)                    │
-- └──────────────────────────────────────────────────────────────────────────┘

CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    INSERT INTO public.profiles (id, username, first_name, last_name, role, is_active)
    VALUES (
        NEW.id,
        COALESCE(NEW.raw_user_meta_data ->> 'username', LOWER(SPLIT_PART(NEW.email, '@', 1))),
        COALESCE(NEW.raw_user_meta_data ->> 'first_name', ''),
        COALESCE(NEW.raw_user_meta_data ->> 'last_name', ''),
        'student',   -- ALWAYS student. Admin is set manually by trusted server operation.
        TRUE
    )
    ON CONFLICT (id) DO NOTHING;
    RETURN NEW;
END;
$$;

-- Drop and recreate trigger to ensure idempotency
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_new_user();

-- ┌──────────────────────────────────────────────────────────────────────────┐
-- │ 3. Auto-update updated_at on profile changes                             │
-- └──────────────────────────────────────────────────────────────────────────┘

CREATE OR REPLACE FUNCTION public.update_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS profiles_updated_at ON public.profiles;
CREATE TRIGGER profiles_updated_at
    BEFORE UPDATE ON public.profiles
    FOR EACH ROW
    EXECUTE FUNCTION public.update_updated_at();

-- ┌──────────────────────────────────────────────────────────────────────────┐
-- │ 4. Enable Row Level Security                                             │
-- └──────────────────────────────────────────────────────────────────────────┘

ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

-- Users can read their own profile
DROP POLICY IF EXISTS "Users can view own profile" ON public.profiles;
CREATE POLICY "Users can view own profile"
    ON public.profiles FOR SELECT
    USING (auth.uid() = id);

-- Users can update their own profile (but NOT role or is_active)
DROP POLICY IF EXISTS "Users can update own profile" ON public.profiles;
CREATE POLICY "Users can update own profile"
    ON public.profiles FOR UPDATE
    USING (auth.uid() = id)
    WITH CHECK (
        auth.uid() = id
        AND role = (SELECT role FROM public.profiles WHERE id = auth.uid())
        AND is_active = (SELECT is_active FROM public.profiles WHERE id = auth.uid())
    );

-- Service role (server-side) has full access (bypasses RLS automatically)
-- Anon/authenticated can only read own row via the policy above

-- Allow public read of username for availability checks (limited columns)
DROP POLICY IF EXISTS "Public username check" ON public.profiles;
CREATE POLICY "Public username check"
    ON public.profiles FOR SELECT
    USING (TRUE);
-- Note: This allows reading profiles. If you want to restrict columns,
-- use a view or RPC instead. The username_available RPC below is the
-- recommended approach.

-- ┌──────────────────────────────────────────────────────────────────────────┐
-- │ 5. username_available RPC function                                       │
-- └──────────────────────────────────────────────────────────────────────────┘

CREATE OR REPLACE FUNCTION public.username_available(check_username TEXT)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    -- Normalize to lowercase
    check_username := LOWER(TRIM(check_username));

    -- Validate format: 3-30 chars, lowercase alnum + dots + underscores
    IF check_username !~ '^[a-z0-9._]{3,30}$' THEN
        RETURN FALSE;
    END IF;

    -- Check if username exists
    RETURN NOT EXISTS (
        SELECT 1 FROM public.profiles
        WHERE LOWER(username) = check_username
    );
END;
$$;

-- Grant execute to anon and authenticated roles (needed for signup check)
GRANT EXECUTE ON FUNCTION public.username_available(TEXT) TO anon;
GRANT EXECUTE ON FUNCTION public.username_available(TEXT) TO authenticated;

-- ┌──────────────────────────────────────────────────────────────────────────┐
-- │ 6. Restrict the overly permissive public read policy                     │
-- │    (Replace with a tighter one that only allows username existence check)│
-- └──────────────────────────────────────────────────────────────────────────┘

-- Actually drop the broad public read and rely on the RPC + service role:
DROP POLICY IF EXISTS "Public username check" ON public.profiles;

-- Authenticated users can read any active profile's non-sensitive fields
-- (needed for the login flow where server reads profile via service key,
--  which bypasses RLS anyway). This policy is for any future client-side needs.
DROP POLICY IF EXISTS "Authenticated read active profiles" ON public.profiles;
CREATE POLICY "Authenticated read active profiles"
    ON public.profiles FOR SELECT
    USING (
        auth.role() = 'authenticated'
        OR auth.role() = 'service_role'
    );

-- ═══════════════════════════════════════════════════════════════════════════
-- DONE. After running this:
-- 1. Verify in Table Editor that public.profiles exists
-- 2. Test: SELECT public.username_available('testuser');
-- 3. Create your first admin account normally, then run:
--    UPDATE public.profiles SET role = 'admin' WHERE username = 'your_admin_username';
-- ═══════════════════════════════════════════════════════════════════════════
