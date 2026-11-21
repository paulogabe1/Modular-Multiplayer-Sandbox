// Defense slot: hides the avatar for a duration.
#pragma once

#include "CoreMinimal.h"
#include "Abilities/AbilityComponentBase.h"
#include "Ability_Invisibility.generated.h"

UCLASS()
class UAbility_Invisibility : public UAbilityComponentBase
{
	GENERATED_BODY()

public:
	virtual float BeginEffect() override;
	virtual void HandleCosmetic(bool bStarting) override;

protected:
	void ApplyInvisibility(bool bInvisible);
};
