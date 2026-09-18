import { MapViewProvider } from '@/lib/context/MapViewContext'
import { OnboardingProvider } from '@/lib/context/OnboardingContext'
import { ProjectProvider } from '@/lib/context/ProjectContext'
import { CompanionGate } from '@/components/shared/CompanionGate'
import type { Metadata } from 'next'
import { Cinzel,Inter } from 'next/font/google'
import './globals.css'

const inter = Inter({ subsets: ['latin'], variable: '--font-inter' })
const cinzel = Cinzel({ subsets: ['latin'], variable: '--font-cinzel' })

export const metadata: Metadata = {
  title: 'Vulcan — Geospatial Datasets',
  description: 'Acquire, validate and explore geospatial datasets',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" className="dark" style={{ height: '100%' }}>
      <head><link rel="stylesheet" href="/cesium/Widgets/widgets.css" /></head>
      <body className={`${inter.variable} ${cinzel.variable} font-sans`} style={{ height: '100%', margin: 0, padding: 0 }}>
            <CompanionGate><ProjectProvider>
              <OnboardingProvider>
                <MapViewProvider>{children}</MapViewProvider>
              </OnboardingProvider>
            </ProjectProvider></CompanionGate>
      </body>
    </html>
  )
}
