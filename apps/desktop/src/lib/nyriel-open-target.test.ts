import { describe, expect, it } from 'vitest'

import {
  normalizeNyrielOpenString,
  pathFromNyrielDeepLink,
  pathFromOpenDeepLink,
  resolveNyrielOpenPath
} from './nyriel-open-target'

describe('normalizeNyrielOpenString', () => {
  it('accepts hash-router paths and strips a leading hash', () => {
    expect(normalizeNyrielOpenString('/index-network/intent/1')).toBe('/index-network/intent/1')
    expect(normalizeNyrielOpenString('#/index-network/intent/1')).toBe('/index-network/intent/1')
  })

  it('maps plugin-scoped nyriel:// deep links to the same path', () => {
    expect(normalizeNyrielOpenString('nyriel://index-network/intent/1')).toBe('/index-network/intent/1')
    expect(normalizeNyrielOpenString('nyriel://index-network/intent/1?focus=true')).toBe(
      '/index-network/intent/1?focus=true'
    )
  })

  it('maps nyriel://open/… deep links by stripping the open host', () => {
    expect(normalizeNyrielOpenString('nyriel://open/index-network/intent/1')).toBe('/index-network/intent/1')
    expect(normalizeNyrielOpenString('nyriel://open/settings/plugins')).toBe('/settings/plugins')
  })

  it('rejects reserved nyriel kinds and unsafe paths', () => {
    expect(normalizeNyrielOpenString('nyriel://blueprint/morning-brief')).toBeNull()
    expect(normalizeNyrielOpenString('nyriel://plugin/install')).toBeNull()
    expect(normalizeNyrielOpenString('https://example.com/x')).toBeNull()
    expect(normalizeNyrielOpenString('/../etc/passwd')).toBeNull()
    expect(normalizeNyrielOpenString('index-network')).toBeNull()
  })
})

describe('resolveNyrielOpenPath', () => {
  it('merges structured path + params', () => {
    expect(resolveNyrielOpenPath({ path: '/index-network/intent/1', params: { focus: 'true' } })).toBe(
      '/index-network/intent/1?focus=true'
    )
  })

  it('resolves href the same as a bare string', () => {
    expect(resolveNyrielOpenPath({ href: 'nyriel://index-network/intent/1' })).toBe('/index-network/intent/1')
  })
})

describe('pathFromNyrielDeepLink', () => {
  it('builds the navigate path from a plugin-scoped deep-link payload', () => {
    expect(pathFromNyrielDeepLink('index-network', 'intent/1')).toBe('/index-network/intent/1')
  })

  it('builds the navigate path from nyriel://open/… payloads', () => {
    expect(pathFromOpenDeepLink('index-network/intent/1')).toBe('/index-network/intent/1')
    expect(pathFromNyrielDeepLink('open', 'agent/42')).toBe('/agent/42')
  })

  it('ignores reserved kinds', () => {
    expect(pathFromNyrielDeepLink('blueprint', 'morning-brief')).toBeNull()
    expect(pathFromNyrielDeepLink('plugin', 'install')).toBeNull()
  })
})
