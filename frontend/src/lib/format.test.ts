import { describe, expect, it } from 'vitest'
import { parseEvents } from '../api'
import { ms, plural, shortHandle, shortPath, splitPath } from './format'

describe('paths', () => {
  it('drops the staging prefix and splits off the last segment', () => {
    expect(splitPath('staging/src/k8s.io/apiserver/pkg/storage')).toEqual({
      lead: 'apiserver/pkg/',
      tail: 'storage',
    })
    expect(splitPath('pkg/kubelet')).toEqual({ lead: 'pkg/', tail: 'kubelet' })
    expect(splitPath('billing')).toEqual({ lead: '', tail: 'billing' })
    expect(shortPath('staging/src/k8s.io/kubectl')).toBe('kubectl')
  })
})

describe('words', () => {
  it('pluralises and formats times', () => {
    expect(plural(1, 'route')).toBe('1 route')
    expect(plural(3, 'route')).toBe('3 routes')
    expect(plural(2, 'person', 'people')).toBe('2 people')
    expect(ms(105.8)).toBe('106 ms')
    expect(ms(3200)).toBe('3.2 s')
    expect(shortHandle('contributor-25b5f')).toBe('25b5f')
  })
})

describe('parseEvents', () => {
  it('keeps a split event for the next chunk', () => {
    const first = parseEvents('data: {"type":"notice","text":"a"}\n\ndata: {"type":"no')
    expect(first.events).toEqual([{ type: 'notice', text: 'a' }])
    const second = parseEvents(first.rest + 'tice","text":"b"}\n\n')
    expect(second.events).toEqual([{ type: 'notice', text: 'b' }])
    expect(second.rest).toBe('')
  })

  it('handles CRLF and skips malformed events', () => {
    const { events } = parseEvents('data: {bad\r\n\r\ndata: {"type":"error","text":"x"}\r\n\r\n')
    expect(events).toEqual([{ type: 'error', text: 'x' }])
  })
})
