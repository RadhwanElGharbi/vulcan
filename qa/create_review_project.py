"""Create an isolated coastal AOI for browser acceptance checks."""
import json
import requests

aoi={'type':'FeatureCollection','features':[{'type':'Feature','properties':{},'geometry':{'type':'Polygon','coordinates':[[[103.86,1.28],[103.875,1.28],[103.875,1.295],[103.86,1.295],[103.86,1.28]]]}}]}
response=requests.post('http://127.0.0.1:8000/api/projects/create',data={'project_name':'QA-Scientific-Review','organization':'ZEUS QA','project_creator':'Automated verification',
                       'drawn_geojson':json.dumps(aoi),'crs_epsg':32648},timeout=60)
if response.status_code != 409:
    response.raise_for_status()
print(response.text)
