select
    slot_id,
    provider_id,
    try_cast(nullif(trim(cast(slot_date as varchar)), '') as date) as slot_date,
    payer_network,
    lower(status) as status
from {{ ref('bronze_appointment_slots') }}
