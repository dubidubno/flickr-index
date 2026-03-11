import flickr_client
flickr = flickr_client.get_api()
resp = flickr_client._api_call(flickr.photosets.getList, user_id='42575154@N00', page=1, per_page=1)
print(resp)
