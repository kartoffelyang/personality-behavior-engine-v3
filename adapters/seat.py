from .instantiate import instantiate


def run(request, parsed, capability, intensity="medium"):
    return instantiate("seat", intensity, request, parsed, capability)
