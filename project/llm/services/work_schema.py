"""Transport structure only: factual and geographic checks remain in FreeWorkBranch."""
from .work import FIELDS


def object_schema(properties):
    return dict(type='object',properties=properties,required=list(properties),additionalProperties=False)


FIELD=object_schema({
    'status':dict(type='string',enum=['known','any','unknown','clarify','declined']),
    'value':dict(anyOf=[dict(type=t) for t in ['string','integer','boolean','null']]+[
        dict(type='array',items=dict(type='string'))]),
    'evidence':dict(type='string'),
    'subject':dict(type='string',enum=['applicant','parent'],description='Whose fact, NOT who speaks: applicant includes a child helped by a parent. parent means facts about the adult, not their child.'),
    'intent':dict(type='string',enum=['desired','previous','excluded','proposal']),
    'alternatives':dict(type='array',items=dict(type='string')),
})
SCHEMA={**object_schema({
    'branch':dict(type='string'),
    'work_fields':object_schema({name:dict(anyOf=[{'$ref':'#/$defs/field'},dict(type='null')]) for name in FIELDS}),
    'work_action':dict(type=['string','null'],enum=['search','remind',None]),
    'work_other':dict(type=['string','null']),
}), '$defs':dict(field=FIELD)}


def valid_structure(data):
    """Reject swallowed/nested fields before applying any part, including with other providers."""
    fields=data.get('work_fields',{})
    if not isinstance(fields,dict):return False
    for name,item in fields.items():
        if name not in FIELDS:return False
        if item is None:continue
        if not isinstance(item,dict) or set(item)-set(FIELD['properties']):return False
        value=item.get('value')
        # Salary is parsed from evidence; legacy responses may carry an ignored object.
        if value is not None and type(value) not in {str,int,bool,list} and not (name=='salary' and isinstance(value,dict)):return False
        if isinstance(value,list) and not all(isinstance(v,str) for v in value):return False
        if item.get('status') not in FIELD['properties']['status']['enum']:return False
        if not isinstance(item.get('evidence'),str):return False
        if not isinstance(item.get('subject'),str) or item['subject'] not in {'applicant','parent'}:return False
        if 'intent' in item and item['intent'] not in FIELD['properties']['intent']['enum']:return False
        if 'alternatives' in item and (not isinstance(item['alternatives'],list) or not all(isinstance(v,str) for v in item['alternatives'])):return False
    other=data.get('work_other')
    if other is None or isinstance(other,str):return True
    # Older saved model replies used the same field envelope as work_fields.
    return (isinstance(other,dict) and set(other)-set(FIELD['properties'])==set()
            and other.get('status')=='known' and isinstance(other.get('value'),str)
            and isinstance(other.get('evidence'),str) and other['value']==other['evidence'])
