select * from {{ ref('fct_order_items') }}
where quantity <= 0 or unit_price <= 0 or line_amount <= 0
