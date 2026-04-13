import re

MAX_POST_LENGTH = 300

def validate_and_rescue_post(content: str) -> str:
    """Checks the quality of the post and attempts to fix common generation issues."""
    rescued_content = content.strip()
    
    # 1. Repetition/Gibberish check (Basic)
    if " + 9 + 9" in rescued_content or re.search(r'(.)\1{5,}', rescued_content):
        raise ValueError("Detected potential gibberish or excessive repetition in post.")
        
    # 2. Length check (Self-correction for refusal messages or failed drafts)
    if len(rescued_content) < 30:
        # If it's too short, it might be a "I can't do that" or a failure. Let's not post it.
        raise ValueError(f"Post is suspiciously short ({len(rescued_content)} chars). Aborting for safety.")

    # 3. Hashtag Rescue Logic
    if "#" not in rescued_content:
        print("Rescue Logic: AI forgot hashtags. Appending defaults...")
        hashtags = "#IT #Tech #Automation"
        if len(rescued_content) + 1 + len(hashtags) <= MAX_POST_LENGTH:
            rescued_content = f"{rescued_content} {hashtags}"
        else:
            # If it doesn't fit, we just let it slide but log it
            print("Could not append hashtags due to length limit.")
            
    return rescued_content

# Test Cases
test_posts = [
    ("This is a solid post with hashtags. #AI #Tech", "Should pass as is"),
    ("This is a post without hashtags.", "Should be rescued with hashtags"),
    ("Short", "Should raise ValueError (too short)"),
    ("aaaaaaaaaaaa", "Should raise ValueError (repetition)"),
    ("Normal post without hashtags that is very long" + "X" * 250, "Should skip rescue (too long)"),
]

for content, description in test_posts:
    print(f"\nTesting: {description}")
    try:
        result = validate_and_rescue_post(content)
        print(f"Result ({len(result)} chars): {result}")
    except Exception as e:
        print(f"Caught expected error: {e}")
