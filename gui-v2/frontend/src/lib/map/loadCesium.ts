import type * as Cesium from 'cesium'

declare global {
  interface Window {
    Cesium?: typeof Cesium
    CESIUM_BASE_URL?: string
  }
}

let pending: Promise<typeof Cesium> | undefined
export function loadCesium(): Promise<typeof Cesium> {
  if (window.Cesium) return Promise.resolve(window.Cesium)
  if (pending) return pending
  window.CESIUM_BASE_URL = '/cesium/'
  pending = new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = '/cesium/Cesium.js'
    script.onload = () => window.Cesium ? resolve(window.Cesium) : reject(new Error('Cesium failed to initialize'))
    script.onerror = () => { pending = undefined; script.remove(); reject(new Error('Could not load Cesium assets')) }
    document.head.appendChild(script)
  })
  return pending
}
