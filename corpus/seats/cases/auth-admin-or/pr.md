auth: fix IsAdmin for service accounts

Service accounts are created verified but have no role, and could not rotate roles.
