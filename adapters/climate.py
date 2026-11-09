from .instantiate import instantiate


def run(request, parsed, capability, intensity="medium"):
    return instantiate("climate", intensity, request, parsed, capability)
