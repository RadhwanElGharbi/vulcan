import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps/api'))
from api.dataset_fetch.research.availability import availability, climate_dates

class AvailabilityTests(unittest.TestCase):
    def row(self,variable='2m_temperature',hours=None):
        return {'product_type':['reanalysis'],'year':['2020'],'month':['02'],'day':['28','29','30'],'variable':[variable],'time':hours or [f'{h:02d}:00' for h in range(24)]}
    def test_leap_dates(self):
        self.assertEqual(climate_dates([self.row()],2020,['2m_temperature']),['2020-02-28','2020-02-29'])
    def test_missing_hour_blocks_date(self):
        self.assertEqual(climate_dates([self.row(hours=['00:00'])],2020,['2m_temperature']),[])
    def test_all_variables_required(self):
        self.assertEqual(climate_dates([self.row()],2020,['2m_temperature','total_precipitation']),[])
    def test_split_hours_combine(self):
        rows=[self.row(hours=[f'{h:02d}:00' for h in range(12)]),self.row(hours=[f'{h:02d}:00' for h in range(12,24)])]
        self.assertEqual(len(climate_dates(rows,2020,['2m_temperature'])),2)
    def test_ensemble_not_reanalysis(self):
        row=self.row();row['product_type']=['ensemble_mean']
        self.assertEqual(climate_dates([row],2020,['2m_temperature']),[])
    def test_worldpop_release_assets_and_scope(self):
        with patch('api.dataset_fetch.research.availability._json',return_value={'data':[{'popyear':2018,'files':['https://provider/2018.tif']},{'popyear':2019,'files':[]},{'popyear':2030,'files':['https://provider/2030.tif']}]}):
            self.assertEqual(availability('worldpop-counts',country='CAN')['years'],[2018])
    def test_worldpop_error_payload(self):
        with patch('api.dataset_fetch.research.availability._json',return_value={'error':'rate limit'}):
            with self.assertRaises(ValueError):availability('worldpop-counts',country='CAN')
    def test_static_years_are_exact_registry_values(self):
        self.assertEqual(availability('ghs-pop-2023a-1km')['years'],[str(y) for y in range(1975,2031,5)])

if __name__=='__main__':unittest.main()
