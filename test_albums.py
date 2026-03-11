import flickr_client
import main
flickr = flickr_client.get_api()
user_id = main.resolve_user_id()
print(f"user_id: {repr(user_id)}")
resp = flickr_client._api_call(flickr.photosets.getList, user_id=user_id, page=1, per_page=1)
print(resp)
