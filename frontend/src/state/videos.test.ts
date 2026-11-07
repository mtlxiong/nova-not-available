import { describe, expect, it } from 'vitest'
import { mergeVideos } from './videos'
import type { VideoInfo } from '../lib/api'

const video = (id: string, name: string): VideoInfo => ({ video_id: id, name, width: 1, height: 1, fps: 1, frame_count: 1, duration_ms: 1 })

describe('mergeVideos', () => {
  it('puts new uploads first and replaces an existing id', () => {
    const existing = [video('a', 'old'), video('b', 'keep')]
    const added = [video('c', 'new'), video('a', 'replaced')]
    expect(mergeVideos(existing, added).map((v) => v.name)).toEqual(['new', 'replaced', 'keep'])
  })
})
