import { api } from './api'
import { useScene } from '../state/ui'

/** Matches the slug the backend derives from a workspace id for Image-Blaster. */
export function worldSlug(modelId: string): string {
  return modelId.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60)
}

let timer: ReturnType<typeof setInterval> | undefined

function showStations() {
  const { scenes } = useScene.getState()
  const procedural = scenes.find((s) => s.kind === 'procedural') ?? scenes.find((s) => s.scene_id === 'procedural')
  useScene.getState().set({
    worldView: false,
    sceneId: procedural?.scene_id ?? 'procedural',
    ...(procedural ? { alignment: procedural.config.alignment } : {}),
  })
}

function showRender(slug: string) {
  const scene = useScene.getState().scenes.find((s) => s.scene_id === slug)
  if (!scene || !useScene.getState().worldView) return false
  useScene.getState().set({ sceneId: slug, alignment: scene.config.alignment, worldBuild: null })
  return true
}

/** Poll a running World Labs job. The station view stays until the render is opted in. */
export function watchWorldGeneration(slug: string) {
  if (timer) clearInterval(timer)
  const tick = async () => {
    const s = await api.generationStatus(slug)
    if (s.status === 'running') {
      const detail = s.detail ?? 'World Labs is still generating'
      useScene.getState().set({ worldBuild: `Building the 3D environment (${detail}).` })
      return
    }
    if (timer) clearInterval(timer)
    timer = undefined
    if (s.status === 'completed') {
      const scenes = await api.scenes()
      useScene.getState().set({ scenes, worldBuild: null })
      if (!showRender(slug)) useScene.getState().set({ worldBuild: null })
    } else if (s.status === 'failed' || s.status === 'unavailable') {
      useScene.getState().set({ worldBuild: s.reason ?? '3D world generation failed', worldView: false })
    } else {
      useScene.getState().set({ worldBuild: null })
    }
  }
  tick().catch(() => undefined)
  timer = setInterval(() => tick().catch(() => undefined), 5000)
}

/** Turn the World Labs render on or off. Stations stay up until a render is ready. */
export async function setWorldView(on: boolean) {
  const modelId = useScene.getState().model?.model_id
  if (!on || !modelId) {
    showStations()
    return
  }
  const slug = worldSlug(modelId)
  useScene.getState().set({ worldView: true })
  const scenes = useScene.getState().scenes
  if (scenes.some((s) => s.scene_id === slug && s.kind === 'image-blaster')) {
    showRender(slug)
    return
  }
  useScene.getState().set({ worldBuild: 'Sending the background photo to World Labs…' })
  try {
    const res = await api.generateWorldFromWorkspace(modelId)
    if (res.status === 'running') watchWorldGeneration(res.slug)
    else if (res.status === 'completed') {
      const next = await api.scenes()
      useScene.getState().set({ scenes: next })
      if (!showRender(res.slug)) useScene.getState().set({ worldBuild: null })
    } else {
      useScene.getState().set({ worldBuild: res.reason ?? `World Labs ${res.status}`, worldView: false })
    }
  } catch (e) {
    useScene.getState().set({ worldBuild: e instanceof Error ? e.message : String(e), worldView: false })
  }
}

/** Resume polling if this workspace already has a generation in flight. */
export function resumeWorldGeneration(modelId: string) {
  const slug = worldSlug(modelId)
  api.generationStatus(slug).then((s) => {
    if (s.status === 'running') watchWorldGeneration(slug)
  }).catch(() => undefined)
}
