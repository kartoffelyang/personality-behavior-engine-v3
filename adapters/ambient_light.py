from .instantiate import instantiate


def run(request, parsed, capability, intensity="medium"):
    return instantiate("ambient_light", intensity, request, parsed, capability)
