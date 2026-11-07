import { create } from 'zustand'
import { api, type VideoInfo } from '../lib/api'

/** New uploads come first. A repeat of an existing id replaces that entry. */
export function mergeVideos(existing: VideoInfo[], added: VideoInfo[]): VideoInfo[] {
  const ids = new Set(added.map((v) => v.video_id))
  return [...added, ...existing.filter((v) => !ids.has(v.video_id))]
}

interface VideoLibrary {
  videos: VideoInfo[]
  refresh: () => Promise<void>
  prepend: (added: VideoInfo[]) => void
}

export const useVideos = create<VideoLibrary>((set) => ({
  videos: [],
  refresh: async () => set({ videos: await api.videos() }),
  prepend: (added) => set((s) => ({ videos: mergeVideos(s.videos, added) })),
}))
