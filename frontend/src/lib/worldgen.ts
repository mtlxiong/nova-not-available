import { api } from './api'
import { useScene } from '../state/ui'

/** Matches the slug the backend derives from a workspace id for Image-Blaster. */
export function worldSlug(modelId: string): string {
  return modelId.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60)
}

let timer: ReturnType<typeof setInterval> | undefined

/** Poll a running World Labs job and switch the 3D view to the splat when it lands. */
export function watchWorldGeneration(slug: string) {
  if (timer) clearInterval(timer)
  const tick = async () => {
    const s = await api.generationStatus(slug)
    if (s.status === 'running') {
      const detail = s.detail ?? 'World Labs is still generating'
      useScene.getState().set({
        worldBuild: `Building the 3D environment (${detail}). The flat floor stays until it finishes, then this view switches to it.`,
      })
      return
    }
    if (timer) clearInterval(timer)
    timer = undefined
    useScene.getState().set({ worldBuild: null })
    if (s.status === 'completed') {
      const scenes = await api.scenes()
      const scene = scenes.find((sc) => sc.scene_id === slug)
      useScene.getState().set({
        scenes,
        ...(scene ? { sceneId: slug, alignment: scene.config.alignment } : {}),
      })
    } else if (s.status === 'failed' || s.status === 'unavailable') {
      useScene.getState().set({ worldBuild: s.reason ?? '3D world generation failed' })
    }
  }
  tick().catch(() => undefined)
  timer = setInterval(() => tick().catch(() => undefined), 5000)
}

/** Resume polling if this workspace already has a generation in flight. */
export function resumeWorldGeneration(modelId: string) {
  const slug = worldSlug(modelId)
  api.generationStatus(slug).then((s) => {
    if (s.status === 'running') watchWorldGeneration(slug)
  }).catch(() => undefined)
}
