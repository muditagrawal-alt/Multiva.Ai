create or replace function public.handle_new_user()
returns trigger as $$
begin
  insert into "User" (
    user_id,
    email,
    full_name,
    subscription_type,
    total_videos_uploaded,
    created_at
  )
  values (
    new.id,
    new.email,
    '',
    'free',
    0,
    now()
  );
  return new;
end;
$$ language plpgsql security definer;


create trigger on_auth_user_created
after insert on auth.users
for each row
execute function public.handle_new_user();