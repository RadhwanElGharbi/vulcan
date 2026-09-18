'use client'
import React from 'react'
export type TourAction = string
// The original demo tour depended on accounts and engineering features.
// Retain action hooks for the project/dataset dialogs without starting that tour.
export function OnboardingProvider({children}: {children: React.ReactNode}) { return <>{children}</> }
const actions = {reportAction: (_action: TourAction) => {}}
export function useOnboarding() { return actions }

