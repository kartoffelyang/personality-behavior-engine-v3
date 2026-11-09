from .instantiate import instantiate


def run(request, parsed, capability, intensity="medium"):
    return instantiate("ecosystem", intensity, request, parsed, capability)
