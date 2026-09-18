import { Download } from 'lucide-react'

/** Archived navigation entry; the standalone dialog and registered action remain intact.
 * To restore, add archivedFetchDatasetsEntry(gis.openFetchDatasets) to Header's dataset entries.
 * Dataset fetching remains available through Digital Twin.
 */
export function archivedFetchDatasetsEntry(openFetchDatasets: () => void) {
  return { label: 'Fetch datasets', icon: Download, run: openFetchDatasets }
}
