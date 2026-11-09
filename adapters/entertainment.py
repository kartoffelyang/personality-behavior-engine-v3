from .instantiate import instantiate


def run(request, parsed, capability, intensity="medium"):
    return instantiate("entertainment", intensity, request, parsed, capability)
