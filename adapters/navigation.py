from .instantiate import instantiate


def run(request, parsed, capability, intensity="medium"):
    return instantiate("navigation", intensity, request, parsed, capability)
