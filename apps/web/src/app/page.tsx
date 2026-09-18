'use client'

import { MainLayout } from '@/components/layout/MainLayout'
import { MapViewer } from '@/components/Map/MapViewer'

export default function Home() {
  return (
    <MainLayout>
      <MapViewer />
    </MainLayout>
  )
}


