"""Human-written Danish for known carrier events; preserve unknown details."""

# Match complete messages only. Never infer delivery/collection from a broad
# sub-status, as that could hide a delay, customs issue or failed delivery.
TRANSLATIONS = {
    'On the way': 'Pakken er under transport',
    'In transit': 'Pakken er under transport',
    'Arrived at Facility': 'Pakken er ankommet til en terminal',
    'Arrived at FedEx hub': 'Pakken er ankommet til FedEx’ sorteringscenter',
    'Departed FedEx hub': 'Pakken har forladt FedEx’ sorteringscenter',
    'Departed from Facility': 'Pakken har forladt terminalen',
    'Departed facility': 'Pakken har forladt terminalen',
    'Arrived at sort facility': 'Pakken er ankommet til et sorteringscenter',
    'At local FedEx facility': 'Pakken er ankommet til den lokale FedEx-terminal',
    'At destination sort facility': 'Pakken er ankommet til sorteringscentret ved destinationen',
    'Picked up': 'Pakken er hentet hos afsenderen',
    'We have your package': 'Fragtfirmaet har modtaget pakken',
    'Out for delivery': 'Pakken er på vej til levering',
    'Out for delivery today': 'Pakken er på vej til levering i dag',
    'On FedEx vehicle for delivery': 'Pakken er på vej til levering med FedEx',
    'Delivered': 'Pakken er leveret',
    'Delivery attempted': 'Fragtfirmaet har forsøgt at levere pakken',
    'Available for pickup': 'Pakken er klar til afhentning',
    'Ready for pickup': 'Pakken er klar til afhentning',
    'Shipment information sent to FedEx': 'FedEx har modtaget oplysninger om pakken',
    'Label Created, Shipper created a label, UPS has not received the package yet.':
        'Afsenderen har oprettet en pakkelabel. UPS har endnu ikke modtaget pakken',
    'Label created': 'Afsenderen har oprettet en pakkelabel',
    'Shipment information received': 'Fragtfirmaet har modtaget oplysninger om pakken',
    # Confirmed old 17TRACK translations, for already stored event history.
    'På vejen ankom jeg til faciliteten': 'Pakken er ankommet til en terminal',
    'Vi har din pakke, ankommet til faciliteten': 'Pakken er ankommet til en terminal',
    'Afgik fra faciliteten': 'Pakken har forladt terminalen',
    'Afgået fra FedEx-hub': 'Pakken har forladt FedEx’ sorteringscenter',
    "Ankommet til FedEx' hub": 'Pakken er ankommet til FedEx’ sorteringscenter',
    'Tog': 'Pakken er hentet hos afsenderen',
    'Forsendelsesoplysninger sendt til FedEx': 'FedEx har modtaget oplysninger om pakken',
    'Label oprettet, afsender har oprettet en label, UPS har endnu ikke modtaget pakken.':
        'Afsenderen har oprettet en pakkelabel. UPS har endnu ikke modtaget pakken',
}


def _key(text):
    return ' '.join(str(text or '').split()).strip().rstrip('.').casefold()


_LOOKUP = {_key(original): danish for original, danish in TRANSLATIONS.items()}


def _known(text):
    key = _key(text)
    if key in _LOOKUP:
        return _LOOKUP[key]
    # UPS combines a general stage and a specific event in one description.
    # Only remove the stage if the entire remaining message is known too.
    for prefix in ('on the way, ', 'we have your package, '):
        if key.startswith(prefix):
            return _LOOKUP.get(key[len(prefix):])
    return None


def localize_description(original, translated=''):
    return (_known(original) or _known(translated) or
            str(translated or original or '').strip())
