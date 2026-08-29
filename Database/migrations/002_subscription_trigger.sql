-- Function: create default subscription for new user
create or replace function public.handle_new_subscription()
returns trigger as $$
begin
  insert into subscriptions (
    subscription_id,
    user_id,
    plan,
    status,
    start_date,
    end_date
  )
  values (
    gen_random_uuid(),
    new.id,
    'free',
    'active',
    now(),
    now() + interval '7 days'
  );
  return new;
end;
$$ language plpgsql security definer;


-- Trigger: runs after new user is created
create trigger on_auth_user_subscription
after insert on auth.users
for each row
execute function public.handle_new_subscription();