import os
from atproto import Client, models
from mastodon import Mastodon
from src.config import MAX_POST_LENGTH_BSKY, MAX_POST_LENGTH_MASTODON

def post_to_bluesky(username, password, content_list, image_data=None, image_alt=""):
    print(f"Broadcasting to Bluesky (@{username})...")
    client = Client()
    client.login(username, password)
    
    parent_ref = None
    root_ref = None
    
    for i, post_text in enumerate(content_list):
        embed = None
        if i == 0 and image_data:
            upload = client.upload_blob(image_data)
            embed = models.AppBskyEmbedImages.Main(
                images=[models.AppBskyEmbedImages.Image(alt=image_alt, image=upload.blob)]
            )
        
        if not parent_ref:
            post = client.send_post(text=post_text, embed=embed)
            root_ref = models.ComAtprotoRepoStrongRef.Main(cid=post.cid, uri=post.uri)
            parent_ref = models.ComAtprotoRepoStrongRef.Main(cid=post.cid, uri=post.uri)
        else:
            reply_ref = models.AppBskyFeedPost.ReplyRef(parent=parent_ref, root=root_ref)
            post = client.send_post(text=post_text, reply_to=reply_ref)
            parent_ref = models.ComAtprotoRepoStrongRef.Main(cid=post.cid, uri=post.uri)
    
    return client

def post_to_mastodon(access_token, api_base_url, content_list, image_data=None, image_alt=""):
    if not access_token:
        print("Skipping Mastodon: No access token.")
        return
    
    print(f"Broadcasting to Mastodon ({api_base_url})...")
    mastodon = Mastodon(access_token=access_token, api_base_url=api_base_url)
    
    media_ids = []
    if image_data:
        media = mastodon.media_post(image_data, mime_type="image/jpeg", description=image_alt)
        media_ids.append(media)
    
    last_id = None
    for i, post_text in enumerate(content_list):
        status = mastodon.status_post(
            status=post_text,
            in_reply_to_id=last_id,
            media_ids=media_ids if i == 0 else None,
            visibility='public'
        )
        last_id = status['id']

def update_profile_bio(client, bio_text):
    """Update only the bio text, preserving existing metadata."""
    try:
        # Fetch existing profile record
        profile_record = client.com.atproto.repo.get_record(
            collection='app.bsky.actor.profile',
            repo=client.me.did,
            rkey='self'
        ).value

        # Update description in the record dictionary
        profile_dict = profile_record.copy() if hasattr(profile_record, 'copy') else dict(profile_record)
        profile_dict['description'] = bio_text

        # Put the record back
        client.com.atproto.repo.put_record(
            collection='app.bsky.actor.profile',
            repo=client.me.did,
            rkey='self',
            record=profile_dict
        )
        print("Updated Bluesky profile bio (v4.2 Robust Sync).")
    except Exception as e:
        print(f"Failed to update Bluesky bio: {e}")

def update_profile_bio_mastodon(token, api_url, bio_text):
    if not token: return
    try:
        mastodon = Mastodon(access_token=token, api_base_url=api_url)
        mastodon.account_update_credentials(note=bio_text)
        print("Updated Mastodon profile bio.")
    except Exception as e:
        print(f"Failed to update Mastodon bio: {e}")
