def percent(part, whole):
    """The share of whole that part is, as a percentage, or 0.0 of nothing."""
    if whole == 0:
        return 0.0
    return part * 100 / whole
