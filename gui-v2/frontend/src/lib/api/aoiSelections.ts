import type { ResearchProduct, ResearchSelection } from './researchClient'

export function withAoiCountries(selection:ResearchSelection, product:ResearchProduct, countries:string[]):ResearchSelection[] {
  if (!Object.values(product.parameters).some(spec=>spec.type==='country')) return [selection]
  const keys=Object.entries(product.parameters).filter(([,spec])=>spec.type==='country').map(([key])=>key)
  const inferred=[...new Set(countries)].sort()
  if (!inferred.length) return [{...selection,parameters:{...selection.parameters,...Object.fromEntries(keys.map(key=>[key,undefined]))}}]
  return inferred.map(country=>({...selection,parameters:{...selection.parameters,...Object.fromEntries(keys.map(key=>[key,country]))}}))
}
