import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps/api'))
from api.dataset_fetch.research.country_selection import country_selections
from api.dataset_fetch.research.contracts import Selection
from api.dataset_fetch.research.registry import registry
class CountrySelectionTests(unittest.TestCase):
 def test_single_country(self):
  result=country_selections([Selection(product_id='worldpop-counts',parameters={'year':2020})],registry(),['CAN'])
  self.assertEqual(result[0].parameters,{'year':2020,'country':'CAN'})
 def test_cross_border(self):
  result=country_selections([Selection(product_id='worldpop-counts',parameters={'year':2020})],registry(),['USA','CAN','CAN'])
  self.assertEqual([x.parameters['country'] for x in result],['CAN','USA'])
 def test_global_product_not_split(self):
  result=country_selections([Selection(product_id='copernicus-glo30',parameters={})],registry(),['CAN','USA'])
  self.assertEqual(len(result),1)
 def test_no_country_not_guessed(self):
  with self.assertRaises(ValueError):country_selections([Selection(product_id='worldpop-counts',parameters={'year':2020})],registry(),[])
 def test_existing_explicit_selection_retained(self):
  selection=Selection(product_id='worldpop-counts',parameters={'year':2020,'country':'CAN'})
  self.assertEqual(country_selections([selection],registry(),['CAN','USA']),[selection])
if __name__=='__main__':unittest.main()
