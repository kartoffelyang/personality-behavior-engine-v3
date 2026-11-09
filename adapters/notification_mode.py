from .instantiate import instantiate


def run(request, parsed, capability, intensity="medium"):
    return instantiate("notification_mode", intensity, request, parsed, capability)
