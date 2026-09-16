import { useQuery } from '@tanstack/react-query'

import { getNyrielConfigRecord, type ProfileScope, profileScopeKey } from '@/nyriel'
import { queryClient, writeCache } from '@/lib/query-client'
import type { NyrielConfigRecord } from '@/types/nyriel'

// One shared cache for the whole profile config record (`GET /api/config`).
// Every settings surface (MCP, model, config) reads and writes through this key
// so a save in one shows in the others, and revisiting a tab paints the cache
// instead of blanking on a fresh fetch.
//
// Distinct from session/hooks/use-nyriel-config.ts, which is side-effecting —
// it pushes personality/cwd/voice/… into the session stores for live chat.
export const NYRIEL_CONFIG_KEY = ['nyriel-config-record'] as const

// Per-scope cache key. The base key (no suffix) is the app-wide active
// profile, unchanged for every caller that passes nothing. An explicit scope —
// the Capabilities scope selector configuring ANOTHER profile, possibly on
// another registered gateway — gets its own suffixed key so switching the
// selector refetches and never paints stale cross-profile config (the
// AGENTS.md scope-in-key rule). profileScopeKey folds a remote pin's
// connection id into the suffix, so two gateways' same-named profiles never
// share a cache row.
export const nyrielConfigKey = (profile?: ProfileScope) =>
  profile == null ? NYRIEL_CONFIG_KEY : ([...NYRIEL_CONFIG_KEY, profileScopeKey(profile)] as const)

// staleTime 0 → serve cache instantly, background-revalidate on every mount.
// `profile` scopes both the query key and the fetch; omitting it preserves the
// exact app-wide behavior (base key, `profileScoped(undefined)` fallback).
export const useNyrielConfigRecord = (profile?: ProfileScope) =>
  useQuery({
    queryKey: nyrielConfigKey(profile),
    // null/undefined both mean "no override" → fetch with undefined so
    // capabilityScoped falls back to the app-wide active profile (passing null
    // would wrongly target the primary backend).
    queryFn: () => getNyrielConfigRecord(profile ?? undefined),
    staleTime: 0
  })

// setNyrielConfigCache writes the app-wide (base-key) record. Pass a profile to
// write the suffixed per-profile cache instead — keeps the selector's optimistic
// write-through landing on the same key its query reads.
export const setNyrielConfigCache = writeCache<NyrielConfigRecord>(NYRIEL_CONFIG_KEY)
export const nyrielConfigCacheWriter = (profile?: ProfileScope) =>
  writeCache<NyrielConfigRecord>(nyrielConfigKey(profile))

export const invalidateNyrielConfig = (profile?: ProfileScope) =>
  queryClient.invalidateQueries({ queryKey: nyrielConfigKey(profile) })
