"""Country-dependent requests are expanded using the project's AOI country set."""
def country_selections(selections, products, countries):
    result=[]
    for selection in selections:
        product=products.get(selection.product_id)
        keys=[key for key,spec in product.parameters.items() if spec['type']=='country'] if product else []
        missing=[key for key in keys if not selection.parameters.get(key)]
        if not missing:
            result.append(selection)
            continue
        if not countries:
            raise ValueError('No country could be inferred from the project AOI for '+selection.product_id)
        for country in sorted(set(countries)):
            result.append(selection.model_copy(update={'parameters':{**selection.parameters,**{key:country for key in missing}}}))
    return result
