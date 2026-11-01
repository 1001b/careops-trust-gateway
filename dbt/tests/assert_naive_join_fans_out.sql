-- The defect fixture must keep inflating slot_id. Zero rows means the fanout disappeared.
select 1 as fanout_not_detected
where (
    select count(*) - count(distinct slot_id)
    from {{ ref('fanout_appointment_capacity') }}
) <= 0
